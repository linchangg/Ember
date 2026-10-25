"""Rust-backed HazardModel with deterministic tempered observation assimilation."""

from __future__ import annotations
from dataclasses import replace
import copy
import hashlib
import json
import time
import numpy as np
from scipy.ndimage import (
    binary_dilation,
    binary_erosion,
    distance_transform_edt,
    convolve,
)
from emberline.contracts import ObservationRecord, GOES_STRONG
from .api import ForecastArrays, Landscape, Weather, ModelConfig, Ignition
from . import bridge
from .bridge import BackendOptions, MODEL_VERSION
from .cdf import summarize_arrivals, region_arrivals
from .filter import ess, temper, systematic_resample, liu_west
from .geometry import GeometryCache, signed_distance
from .likelihoods import LIKELIHOODS, residence_minutes
from .validation import validate_config, validate_landscape, validate_weather


class RustHazardModel:
    """Public contract in api.py; execution limits are constructor-only deployment options."""

    model_version = MODEL_VERSION

    def __init__(self, options: BackendOptions | None = None) -> None:
        self.options = options or BackendOptions()
        self._initialized = False

    def init(
        self, land: Landscape, weather: Weather, cfg: ModelConfig, ign: Ignition
    ) -> None:
        validate_config(cfg)
        self.backend_identity = bridge.verify_backend(self.options)
        self._python_fingerprint = bridge.python_fingerprint()
        land = validate_landscape(land, cfg)
        geometry = GeometryCache(land.grid)
        weather = validate_weather(weather, (land.grid.H, land.grid.W))
        points = np.asarray(ign.points_xy, dtype=np.float64)
        if (
            points.ndim != 2
            or points.shape[1] != 2
            or not len(points)
            or not np.isfinite(points).all()
            or not np.isfinite([ign.t_min, ign.radius_m]).all()
            or ign.t_min < 0
            or ign.radius_m < 0
            or abs(ign.t_min * 60 - round(ign.t_min * 60)) > 1e-4
        ):
            raise ValueError("invalid ignition")
        self.land = land
        self.cfg = replace(cfg, quantiles=tuple(cfg.quantiles))
        self.ign = replace(ign, points_xy=points.copy())
        self.geometry = geometry
        self.burnable = ~np.isin(land.fuel, [0, 92, 93, 98, 99])
        if not cfg.urban:
            self.burnable &= land.fuel != 91
        distance = np.minimum.reduce(
            [(geometry.x - x) ** 2 + (geometry.y - y) ** 2 for x, y in points]
        )
        candidates = np.flatnonzero(
            (distance <= max(ign.radius_m, land.grid.cell_m) ** 2) & self.burnable
        )
        if not len(candidates):
            raise ValueError("ignition uncertainty area contains no burnable grid cell")
        rng = np.random.default_rng([cfg.seed, 0])
        selected = rng.choice(candidates, size=cfg.n_members)
        self._ignition_xy = np.column_stack(
            [geometry.x.ravel()[selected], geometry.y.ravel()[selected]]
        )
        self._theta = self._draw_prior(rng, cfg.n_members)
        self._weights = np.full(cfg.n_members, 1 / cfg.n_members, dtype=np.float64)
        self._arrivals = np.full(
            (cfg.n_members, land.grid.H, land.grid.W), np.inf, dtype=np.float32
        )
        self._anchors = [[] for _ in range(cfg.n_members)]
        self._seeds = [None] * cfg.n_members
        self._weather_changes = [(float(ign.t_min), weather)]
        self._pending_weather = None
        self._history = []
        self._seen = {}
        self._last_issue = None
        self._mask = (distance <= (1.25 * land.grid.cell_m) ** 2) & self.burnable
        self._tau = residence_minutes(land.fuel)
        self.native_diagnostics = {}
        self._initialized = True

    @staticmethod
    def _draw_prior(rng, n):
        return np.column_stack(
            [
                rng.normal(0, 0.35, n),
                rng.normal(0, 0.25, n),
                np.deg2rad(rng.normal(0, 15, n)),
                np.log(np.clip(0.04 + rng.normal(0, 0.015, n), 0.02, 0.12)),
                rng.normal(0, 0.7, n),
            ]
        )

    def _ready(self):
        if not self._initialized:
            raise RuntimeError("call init() or load_state() before using HazardModel")

    def update_weather(self, weather: Weather) -> None:
        self._ready()
        self._pending_weather = validate_weather(
            weather, (self.land.grid.H, self.land.grid.W)
        )

    def _predict(self, now):
        return bridge.solve(
            self.land,
            self.cfg,
            self._weather_changes,
            self._theta,
            self._ignition_xy,
            self.ign.t_min,
            self._anchors,
            self._seeds,
            now,
            self.options,
            self.geometry,
        )

    def _observations(self, now, new_obs):
        now_ms = round((self.land.grid.t_ref_s + now * 60) * 1000)
        validated = []
        new_ids = set()
        for original in new_obs:
            rec = ObservationRecord.model_validate(
                original.model_dump(mode="json")
                if isinstance(original, ObservationRecord)
                else original
            )
            if (
                rec.available_sim_ms > now_ms
                or rec.observed_sim_ms > rec.available_sim_ms
            ):
                raise ValueError(
                    f"observation {rec.obs_id} was unavailable at issue or precedes its observation time"
                )
            if (
                not np.isfinite([rec.trust, rec.confidence]).all()
                or not 0 <= rec.trust <= 1
                or not 0 <= rec.confidence <= 1
            ):
                raise ValueError("trust/confidence must be finite fractions")
            if rec.obs_id in new_ids:
                raise ValueError("duplicate observation id in one batch")
            new_ids.add(rec.obs_id)
            # Branch and status are transport metadata, not physical evidence identity.
            payload = rec.model_dump(
                mode="json", exclude={"branch_id", "status", "cycle_id", "influence"}
            )
            fingerprint = hashlib.sha256(
                json.dumps(payload, sort_keys=True, allow_nan=False).encode()
            ).hexdigest()
            if rec.obs_id in self._seen and self._seen[rec.obs_id] != fingerprint:
                raise ValueError("an already seen observation id changed its evidence")
            observed = (rec.observed_sim_ms / 1000 - self.land.grid.t_ref_s) / 60
            reason = None
            if rec.obs_id in self._seen:
                reason = "duplicate"
            elif observed < self.ign.t_min:
                reason = "before_ignition"
            elif observed < now - self.cfg.history_min:
                reason = "outside_history_window"
            elif rec.trust == 0:
                reason = "zero_trust"
            elif self.cfg.filter == "none":
                reason = "assimilation_disabled"
            if reason is None:
                prepared = self.geometry.prepare(rec)
                if (
                    not prepared["in_domain"]
                    or not (prepared["support"] & self.burnable).any()
                ):
                    reason = "outside_burnable_domain"
            else:
                prepared = None
            validated.append((rec, observed, prepared, reason, fingerprint))
        return sorted(
            validated,
            key=lambda item: (
                item[0].available_sim_ms,
                item[0].observed_sim_ms,
                item[0].obs_id,
            ),
        )

    def _key(self, rec):
        g = json.dumps(rec.geometry.model_dump(mode="json"), sort_keys=True)
        return (
            rec.sensor_id,
            rec.footprint_key or hashlib.sha256(g.encode()).hexdigest(),
        )

    def _strong(self, rec):
        if not rec.hot:
            return False
        if rec.kind == "ground_report":
            return True
        if rec.kind == "viirs_pixel":
            return rec.confidence >= 0.6
        if rec.kind == "goes_pixel" and rec.fdc_class in GOES_STRONG:
            previous = [
                o
                for o in self._history
                if o.kind == "goes_pixel"
                and self._key(o) == self._key(rec)
                and o.observed_sim_ms < rec.observed_sim_ms
            ]
            if previous:
                last = max(previous, key=lambda o: o.observed_sim_ms)
                return last.hot and last.fdc_class in GOES_STRONG
        return False

    def _anchor(self, rec, observed, prepared, arrivals, rng):
        candidates = prepared["support"] & self.burnable
        supported = np.any((arrivals <= observed + 10) & candidates, axis=(1, 2))
        support = float(np.sum(self._weights * supported))
        if support >= 0.1:
            return False, support
        fraction = rec.trust if rec.kind == "ground_report" and rec.trust < 0.9 else 1.0
        selected = np.flatnonzero(rng.random(self.cfg.n_members) < fraction)
        changed = False
        for member in selected:
            burned = (arrivals[member] <= observed) & self.burnable
            if burned.any():
                distance = distance_transform_edt(
                    ~burned, sampling=self.land.grid.cell_m
                )
            else:
                x, y = self._ignition_xy[member]
                distance = np.hypot(self.geometry.x - x, self.geometry.y - y)
            flat = int(np.argmin(np.where(candidates, distance, np.inf)))
            r, c = np.unravel_index(flat, candidates.shape)
            anchor = {
                "time_min": float(observed),
                "row": int(r),
                "col": int(c),
                "obs_id": rec.obs_id,
            }
            # A late observation predating a reinit seed requires an ignition replay.
            if (
                self._seeds[member] is not None
                and observed < self._seeds[member]["time_min"]
            ):
                self._seeds[member] = None
            if anchor not in self._anchors[member]:
                self._anchors[member].append(anchor)
                changed = True
        return changed, support

    def _observed_mask(self, now, observations):
        mask = self._mask.copy()
        for rec, observed, prepared, reason, _ in observations:
            if reason or not rec.hot:
                continue
            trusted = (
                rec.kind in {"viirs_pixel", "thermal_footprint"}
                and rec.confidence >= 0.6
            ) or (rec.kind == "ground_report" and rec.trust >= 0.5)
            if rec.kind == "goes_pixel" and rec.fdc_class in GOES_STRONG:
                scans = [
                    o
                    for o in self._history
                    if o.kind == "goes_pixel" and self._key(o) == self._key(rec)
                ]
                scans = sorted(scans + [rec], key=lambda o: o.observed_sim_ms)[-3:]
                trusted = sum(o.hot and o.fdc_class in GOES_STRONG for o in scans) >= 2
                if trusted:
                    near = (
                        distance_transform_edt(~mask, sampling=self.land.grid.cell_m)
                        <= 2000
                    )
                    mask |= prepared["support"] & near & self.burnable
                    continue
            if trusted:
                mask |= prepared["support"] & self.burnable
        return mask

    def _reinitialize(self, members, mask, now, rng, from_prior):
        if from_prior:
            self._theta[members] = self._draw_prior(rng, len(members))
        for member in members:
            change = int(rng.integers(-1, 2))
            altered = (
                binary_dilation(mask)
                if change == 1
                else binary_erosion(mask)
                if change == -1
                else mask.copy()
            )
            altered &= self.burnable
            if not altered.any():
                self._seeds[member] = None
                self._anchors[member] = []
                continue  # Explicit ignition-prior fallback.
            # LF requires a resolved seed, rather than a one-cell point ignition.
            neighbors = convolve(
                altered.astype(np.uint8),
                np.array([[1, 1, 1], [1, 0, 1], [1, 1, 1]], np.uint8),
                mode="constant",
                cval=0,
            )
            for r, c in np.argwhere(altered & (neighbors == 0)):
                for dr, dc in [(0, 1), (1, 0), (0, -1), (-1, 0)]:
                    rr, cc = r + dr, c + dc
                    if (
                        0 <= rr < mask.shape[0]
                        and 0 <= cc < mask.shape[1]
                        and self.burnable[rr, cc]
                    ):
                        altered[rr, cc] = True
                        break
                else:
                    altered[r, c] = False
            if not altered.any():
                self._seeds[member] = None
                self._anchors[member] = []
                continue
            if altered.all():
                # A fully burned modeled domain needs no propagation; preserve a blocked positive rim
                # only if that rim actually exists. Saturated domains are rejected until supported.
                raise ValueError(
                    "reinit mask saturates entire domain; enlarge the simulation grid"
                )
            phi = signed_distance(altered, self.land.grid.cell_m)
            phi[~self.burnable] = self.land.grid.cell_m
            boundary = altered & ~binary_erosion(altered)
            arrival = np.full(
                mask.shape, max(self.ign.t_min, now - 60), dtype=np.float64
            )
            arrival[boundary] = now
            self._seeds[member] = {
                "phi": phi,
                "arrival": arrival,
                "time_min": float(now),
            }
            self._anchors[member] = []

    def step(
        self, t_now_min: float, new_obs: list[ObservationRecord]
    ) -> ForecastArrays:
        self._ready()
        now = float(t_now_min)
        if (
            not np.isfinite(now)
            or now < self.ign.t_min
            or (self._last_issue is not None and now <= self._last_issue)
            or abs(now / self.cfg.cycle_min - round(now / self.cfg.cycle_min)) > 1e-5
        ):
            raise ValueError(
                "step must be a strictly increasing configured cycle at/after ignition"
            )
        observations = self._observations(now, new_obs)
        # All state changes are transactional, including weather activation and RNG-derived choices.
        snapshot = {
            name: copy.deepcopy(getattr(self, name))
            for name in [
                "_theta",
                "_weights",
                "_ignition_xy",
                "_anchors",
                "_seeds",
                "_weather_changes",
                "_pending_weather",
                "_history",
                "_seen",
                "_mask",
                "_last_issue",
            ]
        }
        started = time.perf_counter()
        try:
            output = self._step(now, observations)
            output.duration_ms = round((time.perf_counter() - started) * 1000)
            return output
        except Exception:
            for name, value in snapshot.items():
                setattr(self, name, value)
            raise

    def _step(self, now, observations):
        if self._pending_weather is not None:
            self._weather_changes.append((now, self._pending_weather))
            self._pending_weather = None
        arrivals, native = self._predict(now)
        rng = np.random.default_rng([self.cfg.seed, round(now / self.cfg.cycle_min)])
        accepted = [o for o in observations if o[3] is None]
        per_obs = []
        for rec, observed, prepared, _, _ in accepted:
            ll = LIKELIHOODS[rec.kind].loglik(
                arrivals,
                rec,
                prepared,
                self._tau,
                self.land.grid.cell_m**2 / 10000,
                observed,
            )
            if not np.isfinite(ll).all():
                raise ValueError("nonfinite observation likelihood")
            per_obs.append(ll)
        ll = np.sum(per_obs, axis=0) if per_obs else np.zeros(self.cfg.n_members)
        alpha = 1.0
        resampled = False
        changed = False
        mask = self._observed_mask(now, observations)
        statuses = {}
        if self.cfg.filter == "pf" and accepted:
            self._weights, alpha = temper(
                np.log(np.maximum(self._weights, 1e-300)), ll, self.cfg.ess_target
            )
            for rec, observed, prepared, _, _ in accepted:
                if self._strong(rec):
                    applied, support = self._anchor(
                        rec, observed, prepared, arrivals, rng
                    )
                    statuses[rec.obs_id] = {
                        "reanchored": applied,
                        "support_before": support,
                    }
                    changed |= applied
            if (
                float(np.max(ll)) / len(accepted) < np.log(0.05)
                and self.cfg.reseed_frac > 0
            ):
                count = max(1, int(self.cfg.reseed_frac * self.cfg.n_members))
                worst = np.argsort(self._weights, kind="stable")[:count]
                parents = rng.choice(self.cfg.n_members, size=count, p=self._weights)
                self._theta[worst] = self._theta[parents].copy()
                self._reinitialize(worst, mask, now, rng, False)
                self._weights[worst] = np.mean(self._weights)
                self._weights /= self._weights.sum()
                changed = True
            # Bisection leaves ESS just ABOVE the floor. Resample at a partial-
            # tempering boundary too, or later cycles stall at alpha ~= 0.
            if (
                alpha < 1.0
                or ess(self._weights) < self.cfg.ess_target * self.cfg.n_members
            ):
                parents = systematic_resample(self._weights, rng)
                self._theta = liu_west(
                    self._theta, self._weights, parents, rng, self.cfg.jitter_a
                )
                self._ignition_xy = self._ignition_xy[parents].copy()
                self._anchors = [copy.deepcopy(self._anchors[p]) for p in parents]
                self._seeds = [copy.deepcopy(self._seeds[p]) for p in parents]
                self._weights.fill(1 / self.cfg.n_members)
                resampled = True
                changed = True
        elif self.cfg.filter == "reinit":
            self._reinitialize(np.arange(self.cfg.n_members), mask, now, rng, True)
            self._weights.fill(1 / self.cfg.n_members)
            changed = True
        if changed:
            arrivals, native = self._predict(now)
        t_q, burned = summarize_arrivals(
            arrivals, self._weights, now, self.cfg.horizon_min, self.cfg.quantiles
        )
        obs_used = []
        ll_index = 0
        for rec, observed, prepared, reason, fingerprint in observations:
            info = {"obs_id": rec.obs_id, "used": reason is None, "ll_spread": 0.0}
            if reason:
                info["reason"] = reason
            else:
                info["ll_spread"] = float(np.ptp(per_obs[ll_index]))
                ll_index += 1
                info.update(statuses.get(rec.obs_id, {}))
                self._history.append(rec)
            self._seen[rec.obs_id] = fingerprint
            obs_used.append(info)
        self._history = [
            o
            for o in self._history
            if (o.observed_sim_ms / 1000 - self.land.grid.t_ref_s) / 60
            >= now - self.cfg.history_min
        ]
        self._mask = mask
        self._arrivals = arrivals
        self._last_issue = now
        self.native_diagnostics = native
        transformed = self._theta.copy()
        transformed[:, [0, 1, 3, 4]] = np.exp(transformed[:, [0, 1, 3, 4]])
        transformed[:, 2] = np.rad2deg(transformed[:, 2])
        means = np.sum(self._weights[:, None] * transformed, axis=0)
        sds = np.sqrt(
            np.sum(self._weights[:, None] * (transformed - means) ** 2, axis=0)
        )
        summary = {
            name: [float(means[k]), float(sds[k])]
            for k, name in enumerate(
                [
                    "ros_mult",
                    "wind_speed_mult",
                    "wind_dir_bias_deg",
                    "dfm_1h",
                    "urban_ros",
                ]
            )
        }
        return ForecastArrays(
            now,
            t_q,
            burned,
            self._weights.copy(),
            ess(self._weights),
            alpha,
            resampled,
            self.cfg.filter,
            summary,
            obs_used,
        )

    def region_arrivals(self, index: np.ndarray, n_regions: int) -> np.ndarray:
        self._ready()
        if self._last_issue is None:
            raise RuntimeError("step before requesting region arrivals")
        return region_arrivals(self._arrivals, index, n_regions)

    def save_state(self) -> bytes:
        self._ready()
        from .checkpoint import save

        return save(self)

    def load_state(self, blob: bytes) -> None:
        from .checkpoint import load

        replacement = load(blob, self.options)
        self.__dict__.update(replacement.__dict__)
