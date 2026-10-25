"""Tests-only HazardModel double: FakeHazardModel (spec/02 §1) on the REAL `ember.hazard.api` dataclasses.

The no-native stand-in for Abby's RustHazardModel (CI builds fire-bridge, laptops may not; the
native path is tests/test_engine_native.py). It keeps the Rust model's call rules, so orchestration
bugs fail here too: spotting must be off, steps are strictly increasing cycle multiples at/after
ignition, no observation from the future, region arrivals only after a step, and save_state /
load_state round-trip bit-exactly, weather included. 16 members, analytic ellipse, uniform
weights, `filter_mode = "none"`.
"""

from __future__ import annotations

import dataclasses
import io
import json
import math
import time

import numpy as np

from ember.hazard.api import ForecastArrays, Landscape, ModelConfig, Weather

MODEL_VERSION = "fake-standin-1"
WEATHER = ("t_min", "u10", "v10", "temp_c", "rh")
STATIC = ("dist", "bearing", "base", "offset")  # init-derived arrays, saved so load_state needs no init


def flat_landscape(ctx) -> Landscape:
    """Flat all-GR2 landscape on a synthetic test scenario's grid. Tests only: Eaton never gets one."""
    from ember.runtime.model_host import model_grid

    g = ctx.grid
    shape = (int(g["ny"]), int(g["nx"]))
    zeros = np.zeros(shape, dtype=np.float32)
    return Landscape(grid=model_grid(ctx), fuel=np.full(shape, 102, dtype=np.int16), slope=zeros,
                     aspect=zeros.copy(), elev=zeros.copy())


# ---------------------------------------------------------------- FakeHazardModel (spec/02 §1)

N_FAKE = 16
HEAD_ROS_M_PER_MIN = 1.2 * 1000 / 60  # 1.2 km/h
LB = 3.0
ECC = math.sqrt(1 - 1 / LB**2)
SCALES = (0.85, 0.95, 1.05, 1.15)  # ±15% ROS
OFFSETS = (-10.0, -10.0 / 3, 10.0 / 3, 10.0)  # ±10° heading
WALK_SD = 0.005  # per-cycle log random walk on ROS, so the state carries history


class FakeHazardModel:
    """16 members, analytic focus-anchored ellipse heading downwind, uniform weights.

    State: per-member ROS multipliers, the earliest-arrival field T (N,H,W) float32 minutes since
    t_ref, the weather in force and the last issue. Each step draws from default_rng([seed,
    cycle_index]), so a restored checkpoint rolled forward reproduces the original run bit for bit.
    """

    def init(self, land, weather, cfg, ign) -> None:
        if cfg.spotting:
            raise ValueError("the Rust surface backend does not implement spotting; set spotting=False")
        g = land.grid
        self.cfg, self.weather, self.t_ref_s = cfg, weather, g.t_ref_s
        self.t_ign = np.float32(ign.t_min)
        self.last: float | None = None
        self.radius = float(ign.radius_m)
        cx, cy = np.asarray(ign.points_xy, dtype=np.float64).reshape(-1, 2).mean(axis=0)
        xs = g.x0 + g.cell_m * (np.arange(g.W) + 0.5)
        ys = g.y0 - g.cell_m * (np.arange(g.H) + 0.5)
        dx, dy = xs[None, :] - cx, ys[:, None] - cy
        self.dist = np.maximum(np.hypot(dx, dy) - self.radius, 0.0)
        self.bearing = np.degrees(np.arctan2(dx, dy)) % 360.0  # cw from grid north
        self.base = np.array([HEAD_ROS_M_PER_MIN * s for s in SCALES for _ in OFFSETS], dtype=np.float32)
        self.offset = np.array([o for _ in SCALES for o in OFFSETS], dtype=np.float32)
        self.mult = np.ones(N_FAKE, dtype=np.float32)
        self.T = np.full((N_FAKE, g.H, g.W), np.inf, dtype=np.float32)

    def update_weather(self, weather) -> None:
        self.weather = weather

    def _heading(self, t_min: float) -> float:
        w = self.weather
        i = int(np.clip(np.searchsorted(np.asarray(w.t_min), t_min, side="right") - 1, 0, len(w.t_min) - 1))
        u, v = float(np.mean(w.u10[i])), float(np.mean(w.v10[i]))
        return math.degrees(math.atan2(u, v)) % 360.0  # direction the wind blows toward

    def step(self, t_now_min: float, new_obs: list) -> ForecastArrays:
        t0 = time.perf_counter()
        cfg = self.cfg
        if (t_now_min < self.t_ign or (self.last is not None and t_now_min <= self.last)
                or abs(t_now_min / cfg.cycle_min - round(t_now_min / cfg.cycle_min)) > 1e-5):
            raise ValueError("step must be a strictly increasing configured cycle at/after ignition")
        now_ms = round((self.t_ref_s + t_now_min * 60) * 1000)
        if any(o.available_sim_ms > now_ms for o in new_obs):
            raise ValueError("an observation was unavailable at issue")
        rng = np.random.default_rng([cfg.seed, round(t_now_min / cfg.cycle_min)])
        self.mult = (self.mult * np.exp(WALK_SD * rng.standard_normal(N_FAKE))).astype(np.float32)
        heading = self._heading(t_now_min)
        theta = np.radians(self.bearing[None] - (heading + self.offset)[:, None, None])
        ros = (self.base * self.mult).astype(np.float64)[:, None, None]
        cand = self.t_ign + self.dist[None] * (1 - ECC * np.cos(theta)) / (ros * (1 - ECC))
        self.T = np.minimum(self.T, cand.astype(np.float32))
        self.last = float(t_now_min)
        return self._arrays(np.float32(t_now_min), new_obs, int((time.perf_counter() - t0) * 1000))

    def _arrays(self, t_now: np.float32, new_obs: list, duration_ms: int) -> ForecastArrays:
        cfg = self.cfg
        srt = np.sort(self.T, axis=0)
        idx = [math.ceil(q * N_FAKE - 1e-9) - 1 for q in cfg.quantiles]
        t_q = (srt[idx] - t_now).astype(np.float32)
        t_q[t_q < 0] = 0.0
        t_q[t_q > cfg.horizon_min] = np.nan
        return ForecastArrays(
            t_issue_min=float(t_now),
            t_q=t_q,
            p_burned_now=(self.T <= t_now).mean(axis=0).astype(np.float32),
            weights=np.full(N_FAKE, 1 / N_FAKE, dtype=np.float64),
            ess=float(N_FAKE),
            tempering_alpha=1.0,
            resampled=False,
            filter_mode="none",
            params_summary={"ros_mult": [round(float(self.mult.mean()), 4), round(float(self.mult.std()), 4)]},
            obs_used=[{"obs_id": o.obs_id, "used": True, "ll_spread": 0.0} for o in new_obs],
            duration_ms=duration_ms,
        )

    def region_arrivals(self, index: np.ndarray, n_regions: int) -> np.ndarray:
        if self.last is None:
            raise RuntimeError("step before requesting region arrivals")
        out = np.full((N_FAKE, n_regions), np.inf, dtype=np.float32)
        for r in range(n_regions):
            mask = index == r
            if mask.any():
                out[:, r] = self.T[:, mask].min(axis=1)
        return out

    def save_state(self) -> bytes:
        """Self-contained like Abby's checkpoint: load_state needs no init."""
        buf = io.BytesIO()
        last = np.nan if self.last is None else self.last
        cfg = np.frombuffer(json.dumps(dataclasses.asdict(self.cfg)).encode(), dtype=np.uint8)
        np.savez_compressed(buf, mult=self.mult, T=self.T, last=np.float64(last), cfg=cfg, t_ign=self.t_ign,
                            t_ref_s=np.int64(self.t_ref_s), **{n: getattr(self, n) for n in STATIC},
                            **{f"wx_{n}": getattr(self.weather, n) for n in WEATHER})
        return buf.getvalue()

    def load_state(self, blob: bytes) -> None:
        with np.load(io.BytesIO(blob)) as z:
            c = json.loads(z["cfg"].tobytes())
            self.cfg = ModelConfig(**{**c, "quantiles": tuple(c["quantiles"])})
            self.t_ign, self.t_ref_s = np.float32(z["t_ign"]), int(z["t_ref_s"])
            for n in STATIC:
                setattr(self, n, z[n].copy())
            self.mult, self.T = z["mult"].copy(), z["T"].copy()
            self.last = None if np.isnan(z["last"]) else float(z["last"])
            self.weather = Weather(*(z[f"wx_{n}"].copy() for n in WEATHER))
