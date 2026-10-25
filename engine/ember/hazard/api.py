"""Frozen HazardModel boundary copied from spec/00_contracts.md §6."""

from dataclasses import dataclass, field
from typing import Protocol, Literal
import numpy as np
from ember.contracts import ObservationRecord


@dataclass(frozen=True)
class Grid:
    crs: str
    x0: float
    y0: float
    cell_m: float
    H: int
    W: int  # row 0 = north; x0,y0 = upper-left
    t_ref_s: int  # model time = float32 minutes since t_ref


@dataclass
class Landscape:  # built by data.load_landscape(scn) (01 §6); Abby may call it directly
    grid: Grid
    fuel: (
        np.ndarray
    )  # (H,W) int16 FBFM40 code (91 = NB1 urban; 92/93/98/99 non-burnable)
    slope: (
        np.ndarray
    )  # (H,W) float32 RISE/RUN (= tan of slope angle). phiS uses slope**2.
    aspect: np.ndarray  # (H,W) float32 deg cw from N, DOWNSLOPE direction
    elev: np.ndarray  # (H,W) float32 m
    canopy_cover: np.ndarray | None = None
    canopy_height: np.ndarray | None = None
    building_frac: np.ndarray | None = None


@dataclass
class Weather:  # from ForcingProvider.weather_as_of() (03 §3); earth-relative winds
    t_min: np.ndarray  # (B,) float32 band start, minutes since t_ref, hourly, ascending
    u10: np.ndarray  # (B,H,W) float32 m/s eastward
    v10: np.ndarray  # (B,H,W) float32 m/s northward
    temp_c: np.ndarray  # (B,)
    rh: np.ndarray  # (B,) 0–1
    # Times after the last band use the last band; times before the first use the first.


@dataclass
class Ignition:
    t_min: float
    points_xy: np.ndarray
    radius_m: float = 300.0  # grid-CRS metres, (K,2)


@dataclass
class ModelConfig:
    n_members: int = 128
    horizon_min: int = 360
    out_step_min: int = 30
    cycle_min: int = 5
    filter: Literal["pf", "reinit", "none"] = "pf"
    ess_target: float = 0.5
    jitter_a: float = 0.97
    reseed_frac: float = 0.25
    spotting: bool = True
    urban: bool = True
    seed: int = 7
    history_min: int = (
        720  # physics keeps its own observation history this long (in state)
    )
    quantiles: tuple[float, ...] = (0.1, 0.3, 0.5, 0.7, 0.9)


@dataclass
class ForecastArrays:
    t_issue_min: float
    t_q: (
        np.ndarray
    )  # (5,H,W) float32 minutes AFTER issue; NaN = beyond horizon; 0 = burned at issue
    p_burned_now: np.ndarray  # (H,W) float32
    weights: np.ndarray  # (N,) float64 normalized
    ess: float
    tempering_alpha: float
    resampled: bool
    filter_mode: str
    params_summary: dict
    obs_used: list[dict] = field(
        default_factory=list
    )  # [{obs_id, used: bool, ll_spread: float}] -> status/influence
    duration_ms: int = 0


class HazardModel(Protocol):
    def init(
        self, land: Landscape, weather: Weather, cfg: ModelConfig, ign: Ignition
    ) -> None: ...
    def update_weather(self, weather: Weather) -> None: ...
    def step(
        self, t_now_min: float, new_obs: list["ObservationRecord"]
    ) -> ForecastArrays:
        ...
        # new_obs = ONLY observations that became available since the previous step; physics keeps history.
        # RNG for this step = np.random.default_rng([cfg.seed, cycle_index]), cycle_index = round(t_now_min / cycle_min).
        # Branch-independent on purpose: a branch differs from its parent ONLY by the injected observations.

    def region_arrivals(self, index: np.ndarray, n_regions: int) -> np.ndarray:
        ...
        # index (H,W) int32 region id or -1 -> (N, n_regions) float32 earliest arrival minutes since t_ref (inf = never)

    def save_state(
        self,
    ) -> bytes: ...  # float32 arrays, np.savez_compressed; bit-identical round trip
    def load_state(self, blob: bytes) -> None: ...


# Implementation identity is separate from the frozen public method/array contract.
MODEL_VERSION = "rust-levelset-pf-1"
