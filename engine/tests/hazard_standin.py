"""Tests-only stand-in for `emberline.hazard.api` (WAVE2 §1.3) until Abby's A0 lands.

`install()` registers this module as `emberline.hazard` / `emberline.hazard.api` in
`sys.modules`, but only if the real import fails. When A0 lands, the real module wins.
The dataclasses are spec/00 §6 verbatim; `FakeHazardModel` follows spec/02 §1, except
`filter_mode = "none"` (the Cycle contract only allows pf | reinit | none).
"""

from __future__ import annotations

import importlib
import io
import math
import sys
import time
import types
from dataclasses import dataclass, field
from typing import Literal, Protocol

import numpy as np

MODEL_VERSION = "fake-standin-1"


# ---------------------------------------------------------------- spec/00 §6, verbatim


@dataclass(frozen=True)
class Grid:
    crs: str; x0: float; y0: float; cell_m: float; H: int; W: int   # row 0 = north; x0,y0 = upper-left
    t_ref_s: int                                                    # model time = float32 minutes since t_ref


@dataclass
class Landscape:                     # built by data.load_landscape(scn) (01 §6); Abby may call it directly
    grid: Grid
    fuel: np.ndarray                 # (H,W) int16 FBFM40 code (91 = NB1 urban; 92/93/98/99 non-burnable)
    slope: np.ndarray                # (H,W) float32 RISE/RUN (= tan of slope angle). phiS uses slope**2.
    aspect: np.ndarray               # (H,W) float32 deg cw from N, DOWNSLOPE direction
    elev: np.ndarray                 # (H,W) float32 m
    canopy_cover: np.ndarray | None = None
    canopy_height: np.ndarray | None = None
    building_frac: np.ndarray | None = None


@dataclass
class Weather:                       # from ForcingProvider.weather_as_of() (03 §3); earth-relative winds
    t_min: np.ndarray                # (B,) float32 band start, minutes since t_ref, hourly, ascending
    u10: np.ndarray                  # (B,H,W) float32 m/s eastward
    v10: np.ndarray                  # (B,H,W) float32 m/s northward
    temp_c: np.ndarray               # (B,)
    rh: np.ndarray                   # (B,) 0–1
    # Times after the last band use the last band; times before the first use the first.


@dataclass
class Ignition:
    t_min: float; points_xy: np.ndarray; radius_m: float = 300.0   # grid-CRS metres, (K,2)


@dataclass
class ModelConfig:
    n_members: int = 128; horizon_min: int = 360; out_step_min: int = 30; cycle_min: int = 5
    filter: Literal["pf", "reinit", "none"] = "pf"
    ess_target: float = 0.5; jitter_a: float = 0.97; reseed_frac: float = 0.25
    spotting: bool = True; urban: bool = True; seed: int = 7
    history_min: int = 720           # physics keeps its own observation history this long (in state)
    quantiles: tuple[float, ...] = (0.1, 0.3, 0.5, 0.7, 0.9)


@dataclass
class ForecastArrays:
    t_issue_min: float
    t_q: np.ndarray                  # (5,H,W) float32 minutes AFTER issue; NaN = beyond horizon; 0 = burned at issue
    p_burned_now: np.ndarray         # (H,W) float32
    weights: np.ndarray              # (N,) float64 normalized
    ess: float; tempering_alpha: float; resampled: bool; filter_mode: str
    params_summary: dict
    obs_used: list[dict] = field(default_factory=list)   # [{obs_id, used: bool, ll_spread: float}] -> status/influence
    duration_ms: int = 0


class HazardModel(Protocol):
    def init(self, land: Landscape, weather: Weather, cfg: ModelConfig, ign: Ignition) -> None: ...
    def update_weather(self, weather: Weather) -> None: ...
    def step(self, t_now_min: float, new_obs: list) -> ForecastArrays: ...
    def region_arrivals(self, index: np.ndarray, n_regions: int) -> np.ndarray: ...
    def save_state(self) -> bytes: ...
    def load_state(self, blob: bytes) -> None: ...


# ---------------------------------------------------------------- install


def install() -> types.ModuleType:
    """Return the real `emberline.hazard.api` if importable, else register this module as it."""
    try:
        return importlib.import_module("emberline.hazard.api")
    except ImportError:
        pass
    pkg = types.ModuleType("emberline.hazard")
    pkg.__path__ = []  # a package, so `emberline.hazard.api` resolves from sys.modules
    pkg.MODEL_VERSION = MODEL_VERSION
    pkg.api = sys.modules[__name__]
    sys.modules["emberline.hazard"] = pkg
    sys.modules["emberline.hazard.api"] = sys.modules[__name__]
    return sys.modules[__name__]


def _api():
    return importlib.import_module("emberline.hazard.api")


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

    State: per-member ROS multipliers plus the earliest-arrival field T (N,H,W) float32,
    minutes since t_ref. Each step draws from default_rng([seed, cycle_index]), so a
    restored checkpoint rolled forward reproduces the original run bit for bit.
    """

    def init(self, land, weather, cfg, ign) -> None:
        g = land.grid
        self.cfg = cfg
        self.weather = weather
        self.t_ign = np.float32(ign.t_min)
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
        rng = np.random.default_rng([cfg.seed, round(t_now_min / cfg.cycle_min)])
        self.mult = (self.mult * np.exp(WALK_SD * rng.standard_normal(N_FAKE))).astype(np.float32)
        heading = self._heading(t_now_min)
        theta = np.radians(self.bearing[None] - (heading + self.offset)[:, None, None])
        ros = (self.base * self.mult).astype(np.float64)[:, None, None]
        cand = self.t_ign + self.dist[None] * (1 - ECC * np.cos(theta)) / (ros * (1 - ECC))
        self.T = np.minimum(self.T, cand.astype(np.float32))
        return self._arrays(np.float32(t_now_min), new_obs, int((time.perf_counter() - t0) * 1000))

    def _arrays(self, t_now: np.float32, new_obs: list, duration_ms: int):
        cfg = self.cfg
        srt = np.sort(self.T, axis=0)
        idx = [math.ceil(q * N_FAKE - 1e-9) - 1 for q in cfg.quantiles]
        t_q = (srt[idx] - t_now).astype(np.float32)
        t_q[t_q < 0] = 0.0
        t_q[t_q > cfg.horizon_min] = np.nan
        return _api().ForecastArrays(
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
        out = np.full((N_FAKE, n_regions), np.inf, dtype=np.float32)
        for r in range(n_regions):
            mask = index == r
            if mask.any():
                out[:, r] = self.T[:, mask].min(axis=1)
        return out

    def save_state(self) -> bytes:
        buf = io.BytesIO()
        np.savez_compressed(buf, mult=self.mult, T=self.T)
        return buf.getvalue()

    def load_state(self, blob: bytes) -> None:
        with np.load(io.BytesIO(blob)) as z:
            self.mult, self.T = z["mult"].copy(), z["T"].copy()
