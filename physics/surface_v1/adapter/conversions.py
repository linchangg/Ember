"""Grid, time, fuel, moisture, and Rust bundle conversions.

Row reversal is separate from coordinate reprojection. This module does not
resample, rotate wind, or choose unresolved production policies.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from adapter.errors import UnsupportedMapping, ValidationError

# Planned application quantiles from spec/00_contracts.md. The encoder that
# would write arrival_q.bin has not landed; callers must pass levels explicitly.
SPECIFIED_APPLICATION_QUANTILES: tuple[float, ...] = (0.1, 0.3, 0.5, 0.7, 0.9)

CONTRACT_CRS = "EPSG:32611"
RUST_MIN_EXTENT = 3
# Float32 minutes stay inside a range where a minute is still a meaningful
# fraction of the unit in the last place. A multi-year offset is rejected
# instead of being silently rounded.
MAX_ABS_MINUTES = 1_000_000.0
# Two float32 ulps. A stored float32 minute value round-trips exactly; this
# bound is for comparing an exact f64 minute value with its float32 storage.
FLOAT32_ROUNDTRIP_ULPS = 2
MS_PER_MINUTE = 60_000
SECONDS_PER_MINUTE = 60.0

_SURFACE_ROOT = Path(__file__).resolve().parents[1]
FUEL_CATALOG_PATH = _SURFACE_ROOT / "docs" / "spec" / "fuel_models.csv"

# Rust v1 timestamp parser rejects anything but whole-second Zulu form.
RUST_TIMESTAMP_INCOMPATIBILITIES: tuple[str, ...] = (
    "timestamps must be whole seconds in YYYY-MM-DDTHH:MM:SSZ",
    "numeric offsets, fractional seconds, and years before 1970 are rejected",
    "the adapter does not truncate milliseconds to satisfy that parser",
)


@dataclass(frozen=True)
class AppGrid:
    """Application grid as specified in spec/00 §2 and spec/01 §1.

    ``x0_m`` and ``y0_m`` are the upper-left outer edge, not a cell center.
    Row 0 is north. ``origin`` must stay ``upper_left_outer_edge``; a center
    origin is rejected rather than guessed.
    """

    crs: str
    x0_m: float
    y0_m: float
    cell_m: float
    height: int
    width: int
    origin: str = "upper_left_outer_edge"


@dataclass(frozen=True)
class RustGrid:
    """Rust ``GridSpec``: row 0 is south, origin is the lower-left outer edge."""

    crs: str
    x0_m: float
    y0_m: float
    cell_size_m: float
    height: int
    width: int


@dataclass(frozen=True)
class TimeOrigins:
    """The clocks this layer keeps distinct. Equality is not assumed."""

    t_ref_ms: int
    t_start_ms: int
    t_issue_ms: int


@dataclass(frozen=True)
class ReferenceBinding:
    """Research record that t_ref survives a Rust checkpoint unchanged.

    Rust checkpoints store ``start_utc`` and do not store ``t_ref``. This
    binding is the adapter's record of the application reference time.
    """

    t_ref_ms: int
    t_start_utc: str
    model: str
    schema_version: int


@dataclass(frozen=True)
class WeatherFrame:
    """One environment frame in application row order.

    ``time_s`` is seconds after the Rust origin when the frame becomes active.
    ``valid_time_ms`` is the observation or valid time. ``available_time_ms``
    is when a live system could have received it. Availability is checked
    against the issue time and, separately, against the Rust origin.
    """

    time_s: float
    valid_time_ms: int
    available_time_ms: int
    wind_east_m_s: np.ndarray | float
    wind_north_m_s: np.ndarray | float
    m1: np.ndarray | float
    m10: np.ndarray | float
    m100: np.ndarray | float
    mlh: np.ndarray | float
    mlw: np.ndarray | float


@dataclass(frozen=True)
class ResearchBundle:
    """Arrays already in Rust order, ready to write. No application policy is implied."""

    grid: RustGrid
    elevation_m: np.ndarray
    fuel_id: np.ndarray
    valid: np.ndarray
    burnable: np.ndarray
    phi: np.ndarray
    frames: tuple[dict[str, np.ndarray], ...]
    frame_time_s: tuple[float, ...]
    frame_valid_utc: tuple[str, ...]
    frame_available_utc: tuple[str, ...]
    start_utc: str
    incident_id: str
    horizons_s: tuple[float, ...]
    coverage_end_s: float
    cfl: float
    max_step_s: float
    wind_limit: str
    ellipse_max_lb: float
    wind_reference: str


def _require_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{name} must be an int")
    return value


def _finite(name: str, value: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{name} must be a real number") from exc
    if not math.isfinite(number):
        raise ValidationError(f"{name} must be finite")
    return number


def geographic_crs(crs: str) -> bool:
    """Mirror the Rust geographic rejection and also reject other lon/lat names."""
    token = crs.strip().upper().replace(" ", "")
    return any(
        mark in token
        for mark in ("4326", "WGS84", "LATLON", "LAT/LON", "CRS84", "EPSG:4269", "NAD83")
    )


def validate_crs(crs: str, *, allow_research_metric_crs: bool = False) -> str:
    if not isinstance(crs, str) or not crs.strip():
        raise ValidationError("CRS is required")
    if geographic_crs(crs):
        raise ValidationError(f"unsupported geographic CRS {crs!r}; reprojection is not performed")
    if crs == CONTRACT_CRS:
        return crs
    if allow_research_metric_crs:
        # Research fixtures may use a metric label such as LOCAL_METRIC_TEST.
        # The same string is passed through; nothing is reprojected.
        return crs
    raise ValidationError(
        f"unsupported CRS {crs!r}; the specified application contract grid is {CONTRACT_CRS}"
    )


def validate_app_grid(grid: AppGrid, *, allow_research_metric_crs: bool = False) -> AppGrid:
    if grid.origin != "upper_left_outer_edge":
        raise ValidationError(
            f"application origin {grid.origin!r} is not the specified upper-left outer edge"
        )
    validate_crs(grid.crs, allow_research_metric_crs=allow_research_metric_crs)
    height = _require_int("height", grid.height)
    width = _require_int("width", grid.width)
    if height < RUST_MIN_EXTENT or width < RUST_MIN_EXTENT:
        raise ValidationError(
            f"grid extent {height}x{width} is below the Rust minimum {RUST_MIN_EXTENT}x{RUST_MIN_EXTENT}"
        )
    cell = _finite("cell_m", grid.cell_m)
    if cell <= 0.0:
        raise ValidationError("cell_m must be positive")
    _finite("x0_m", grid.x0_m)
    _finite("y0_m", grid.y0_m)
    return grid


def validate_rust_grid(grid: RustGrid, *, allow_research_metric_crs: bool = False) -> RustGrid:
    validate_crs(grid.crs, allow_research_metric_crs=allow_research_metric_crs)
    if grid.height < RUST_MIN_EXTENT or grid.width < RUST_MIN_EXTENT:
        raise ValidationError(
            f"grid extent {grid.height}x{grid.width} is below the Rust minimum"
        )
    cell = _finite("cell_size_m", grid.cell_size_m)
    if cell <= 0.0:
        raise ValidationError("cell_size_m must be positive")
    _finite("x0_m", grid.x0_m)
    _finite("y0_m", grid.y0_m)
    return grid


def app_grid_to_rust(grid: AppGrid, *, allow_research_metric_crs: bool = False) -> RustGrid:
    """Lower-left outer edge of a north-first upper-left outer edge.

    ``x0_rust = x0_app`` and ``y0_rust = y0_app - height * cell_m``.
    Square cells only: Rust stores one ``cell_size_m`` for both axes.
    """
    grid = validate_app_grid(grid, allow_research_metric_crs=allow_research_metric_crs)
    return RustGrid(
        crs=grid.crs,
        x0_m=float(grid.x0_m),
        y0_m=float(grid.y0_m) - grid.height * float(grid.cell_m),
        cell_size_m=float(grid.cell_m),
        height=grid.height,
        width=grid.width,
    )


def rust_grid_to_app(grid: RustGrid, *, allow_research_metric_crs: bool = False) -> AppGrid:
    grid = validate_rust_grid(grid, allow_research_metric_crs=allow_research_metric_crs)
    return AppGrid(
        crs=grid.crs,
        x0_m=float(grid.x0_m),
        y0_m=float(grid.y0_m) + grid.height * float(grid.cell_size_m),
        cell_m=float(grid.cell_size_m),
        height=grid.height,
        width=grid.width,
        origin="upper_left_outer_edge",
    )


def assert_grids_correspond(
    app: AppGrid,
    rust: RustGrid,
    *,
    allow_research_metric_crs: bool = False,
) -> None:
    """Reject any difference that would require resampling or reprojection."""
    expected = app_grid_to_rust(app, allow_research_metric_crs=allow_research_metric_crs)
    if rust.crs != expected.crs:
        raise ValidationError(
            f"CRS mismatch {app.crs!r} vs {rust.crs!r}; grids are not reprojected",
            code="grid_misaligned",
        )
    if (
        rust.height != expected.height
        or rust.width != expected.width
        or rust.cell_size_m != expected.cell_size_m
        or rust.x0_m != expected.x0_m
        or rust.y0_m != expected.y0_m
    ):
        raise ValidationError(
            "application and Rust grids are not the same metric lattice",
            code="grid_misaligned",
        )


def app_cell_centers(grid: AppGrid) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(x_by_col, y_by_row)`` with row 0 at the north edge.

    Matches spec/01 §1: ``x = x0 + (c+1/2)*cell``, ``y = y0 - (r+1/2)*cell``.
    """
    grid = validate_app_grid(grid, allow_research_metric_crs=True)
    cell = float(grid.cell_m)
    x = grid.x0_m + (np.arange(grid.width, dtype=np.float64) + 0.5) * cell
    y = grid.y0_m - (np.arange(grid.height, dtype=np.float64) + 0.5) * cell
    return x, y


def rust_cell_centers(grid: RustGrid) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(x_by_col, y_by_row)`` with row 0 at the south edge."""
    grid = validate_rust_grid(grid, allow_research_metric_crs=True)
    cell = float(grid.cell_size_m)
    x = grid.x0_m + (np.arange(grid.width, dtype=np.float64) + 0.5) * cell
    y = grid.y0_m + (np.arange(grid.height, dtype=np.float64) + 0.5) * cell
    return x, y


def flip_rows_app_to_rust(values: np.ndarray) -> np.ndarray:
    """Reverse the row axis. ``A_rust[r, c] = A_app[H-1-r, c]``.

    Values and dtype are preserved. The result is C-contiguous, which is the
    row-major order Rust reads. Wind components are not negated.
    """
    array = np.asarray(values)
    if array.ndim < 2:
        raise ValidationError("a spatial field needs row and column axes")
    if array.shape[-2] < 1 or array.shape[-1] < 1:
        raise ValidationError("spatial field has an empty axis")
    return np.ascontiguousarray(np.flip(array, axis=-2))


def flip_rows_rust_to_app(values: np.ndarray) -> np.ndarray:
    """Row reversal is an involution. The same rule converts both directions."""
    return flip_rows_app_to_rust(values)


def require_spatial_shape(values: np.ndarray, grid: AppGrid | RustGrid, name: str) -> np.ndarray:
    array = np.asarray(values)
    expected = (grid.height, grid.width)
    if array.shape[-2:] != expected:
        raise ValidationError(
            f"{name} spatial shape {array.shape[-2:]} != {expected}",
            code="shape_mismatch",
        )
    return array


def _spatial_to_rust(
    values: np.ndarray | float,
    grid: AppGrid,
    name: str,
    dtype: np.dtype,
) -> np.ndarray:
    if np.isscalar(values):
        array = np.full((grid.height, grid.width), values, dtype=dtype)
    else:
        array = require_spatial_shape(np.asarray(values), grid, name)
        if array.shape != (grid.height, grid.width):
            raise ValidationError(
                f"{name} must be a single 2D field, got shape {array.shape}",
                code="shape_mismatch",
            )
    flipped = flip_rows_app_to_rust(array)
    return np.ascontiguousarray(flipped, dtype=dtype)


def convert_elevation(elevation_m: np.ndarray, grid: AppGrid) -> np.ndarray:
    """Flip rows, then widen to float64. No resampling."""
    array = require_spatial_shape(np.asarray(elevation_m), grid, "elevation")
    if array.shape != (grid.height, grid.width):
        raise ValidationError("elevation must be one 2D field", code="shape_mismatch")
    if not np.issubdtype(array.dtype, np.floating):
        raise ValidationError("elevation must be floating point")
    if not np.isfinite(array).all():
        raise ValidationError("elevation must be finite")
    flipped = flip_rows_app_to_rust(array)
    return np.ascontiguousarray(flipped, dtype="<f8")


def load_catalog_ids(path: Path | None = None) -> set[int]:
    catalog = path or FUEL_CATALOG_PATH
    ids: set[int] = set()
    with catalog.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "fuel_id" not in reader.fieldnames:
            raise ValidationError(f"fuel catalog {catalog} has no fuel_id column")
        for row in reader:
            ids.add(int(row["fuel_id"]))
    if not ids:
        raise ValidationError("fuel catalog is empty")
    return ids


def convert_fuel_ids(
    fuel_id: np.ndarray,
    valid: np.ndarray,
    burnable: np.ndarray,
    grid: AppGrid,
    *,
    policy: str,
    catalog_ids: set[int] | None = None,
) -> np.ndarray:
    """Pass catalog identifiers through. FBFM40 and NB91 are not translated.

    ``policy`` must be ``passthrough_catalog_id``. Cells that are both valid
    and burnable must already use an id in the Rust catalog. Invalid and
    non-burnable cells are not looked up, matching ``validate_inputs``.
    """
    if policy != "passthrough_catalog_id":
        raise UnsupportedMapping(
            f"fuel policy {policy!r} is not implemented; FBFM40 and urban NB91 "
            "are not mapped onto the Rust catalog"
        )
    ids = require_spatial_shape(np.asarray(fuel_id), grid, "fuel_id")
    mask_valid = require_spatial_shape(np.asarray(valid), grid, "valid")
    mask_burn = require_spatial_shape(np.asarray(burnable), grid, "burnable")
    if ids.shape != (grid.height, grid.width):
        raise ValidationError("fuel_id must be one 2D field", code="shape_mismatch")
    if not np.issubdtype(ids.dtype, np.integer):
        raise ValidationError("fuel_id must be an integer array")
    if ids.min() < 0 or ids.max() > np.iinfo(np.uint16).max:
        raise ValidationError("fuel_id does not fit in uint16")
    known = catalog_ids if catalog_ids is not None else load_catalog_ids()
    modeled = (np.asarray(mask_valid) == 1) & (np.asarray(mask_burn) == 1)
    unknown = sorted({int(v) for v in ids[modeled].tolist() if int(v) not in known})
    if unknown:
        raise ValidationError(
            f"burnable cells use fuel ids {unknown} that are absent from the Rust catalog; "
            "no FBFM40 translation is applied"
        )
    return np.ascontiguousarray(flip_rows_app_to_rust(ids), dtype="<u2")


def convert_mask(mask: np.ndarray, grid: AppGrid, name: str) -> np.ndarray:
    """Flip a 0/1 mask without changing values."""
    array = require_spatial_shape(np.asarray(mask), grid, name)
    if array.shape != (grid.height, grid.width):
        raise ValidationError(f"{name} must be one 2D field", code="shape_mismatch")
    if array.dtype == np.bool_:
        array = array.astype(np.uint8)
    if not np.isin(array, (0, 1)).all():
        raise ValidationError(f"{name} values must be 0 or 1")
    flipped = flip_rows_app_to_rust(array.astype(np.uint8, copy=False))
    return np.ascontiguousarray(flipped, dtype=np.uint8)


def convert_wind_components(
    east_m_s: np.ndarray | float,
    north_m_s: np.ndarray | float,
    grid: AppGrid,
    *,
    components_are_midflame: bool,
) -> tuple[np.ndarray, np.ndarray]:
    """Flip wind fields. East stays east and north stays north.

    The only Rust wind reference is midflame. This function does not reduce
    10 m winds. Callers must set ``components_are_midflame`` when the values
    are already the midflame vector the research bundle will label as such.
    """
    if not components_are_midflame:
        raise UnsupportedMapping(
            "u10/v10 to midflame reduction is unresolved; refusing to relabel 10 m winds as midflame"
        )
    east = _spatial_to_rust(east_m_s, grid, "wind_east", np.dtype("<f8"))
    north = _spatial_to_rust(north_m_s, grid, "wind_north", np.dtype("<f8"))
    if not np.isfinite(east).all() or not np.isfinite(north).all():
        raise ValidationError("wind components must be finite")
    return east, north


def wind_components_from_direction(
    speed_m_s: np.ndarray | float,
    direction_deg_cw_from_north: np.ndarray | float,
    *,
    speed_is_midflame: bool,
) -> tuple[np.ndarray, np.ndarray]:
    """Decompose a toward-direction into east/north. This is not a row flip.

    ``east = speed * sin(direction)``, ``north = speed * cos(direction)``,
    with direction clockwise from north. The result is not spatially flipped.
    """
    if not speed_is_midflame:
        raise UnsupportedMapping(
            "direction decomposition does not apply a 10 m to midflame reduction"
        )
    speed = np.asarray(speed_m_s, dtype=np.float64)
    direction = np.asarray(direction_deg_cw_from_north, dtype=np.float64)
    if speed.shape != direction.shape and speed.ndim != 0 and direction.ndim != 0:
        raise ValidationError("wind speed and direction shapes differ", code="shape_mismatch")
    if not np.isfinite(speed).all() or not np.isfinite(direction).all():
        raise ValidationError("wind speed and direction must be finite")
    if np.any(speed < 0.0):
        raise ValidationError("wind speed must be nonnegative")
    radians = np.deg2rad(direction)
    east = np.ascontiguousarray(speed * np.sin(radians))
    north = np.ascontiguousarray(speed * np.cos(radians))
    return east, north


def convert_moisture_fractions(
    m1: np.ndarray | float,
    m10: np.ndarray | float,
    m100: np.ndarray | float,
    mlh: np.ndarray | float,
    mlw: np.ndarray | float,
    grid: AppGrid,
) -> dict[str, np.ndarray]:
    """Flip explicit moisture fractions. Relative humidity is not converted."""
    out: dict[str, np.ndarray] = {}
    for name, value in (("m1", m1), ("m10", m10), ("m100", m100), ("mlh", mlh), ("mlw", mlw)):
        field = _spatial_to_rust(value, grid, name, np.dtype("<f8"))
        if not np.isfinite(field).all():
            raise ValidationError(f"{name} must be finite")
        if np.any(field < 0.0):
            raise ValidationError(f"{name} must be a nonnegative fraction")
        out[name] = field
    return out


def reject_humidity_as_moisture(rh: object) -> None:
    """RH is an application weather field. It is not a fuel-moisture fraction."""
    raise UnsupportedMapping(
        "relative humidity is not converted to dead or live fuel moisture; "
        f"received rh={rh!r}"
    )


def float32_roundtrip_tolerance_minutes(stored_minutes: float) -> float:
    """Absolute tolerance of ``FLOAT32_ROUNDTRIP_ULPS`` at ``stored_minutes``."""
    stored = np.float32(stored_minutes)
    upward = np.nextafter(stored, np.float32(np.inf))
    ulp = float(upward - stored)
    if ulp == 0.0:
        # +inf neighborhood; the range check should already have rejected this.
        raise ValidationError("float32 minute value has no finite spacing")
    return FLOAT32_ROUNDTRIP_ULPS * ulp


def minutes_since_t_ref(t_start_ms: int, t_ref_ms: int, duration_s: float) -> np.float32:
    """Application minutes since ``t_ref`` for a duration after the Rust origin.

    ``(t_start_ms - t_ref_ms) / 60000 + duration_s / 60``.

    The subtraction uses Python integers. The only rounding is the final
    float32 cast. Origins are not assumed equal.
    """
    start = _require_int("t_start_ms", t_start_ms)
    ref = _require_int("t_ref_ms", t_ref_ms)
    seconds = _finite("duration_s", duration_s)
    if abs(seconds) > 1.0e12:
        raise ValidationError("duration_s is outside the supported range")
    exact = (start - ref) / MS_PER_MINUTE + seconds / SECONDS_PER_MINUTE
    if not math.isfinite(exact) or abs(exact) > MAX_ABS_MINUTES:
        raise ValidationError("application minutes are outside the supported float32 range")
    return np.float32(exact)


def minutes_after_issue(
    t_start_ms: int,
    t_ref_ms: int,
    t_issue_ms: int,
    duration_s: float,
) -> np.float32:
    """Minutes after the forecast issue, computed in float64 before the cast.

    This is the clock described for ``ForecastArrays.t_q`` in the spec. It is
    not the default research arrival clock; callers must request it.
    """
    issue = _require_int("t_issue_ms", t_issue_ms)
    ref = _require_int("t_ref_ms", t_ref_ms)
    issue_min = (issue - ref) / MS_PER_MINUTE
    exact = (
        (_require_int("t_start_ms", t_start_ms) - ref) / MS_PER_MINUTE
        + _finite("duration_s", duration_s) / SECONDS_PER_MINUTE
        - issue_min
    )
    if not math.isfinite(exact) or abs(exact) > MAX_ABS_MINUTES:
        raise ValidationError("minutes after issue are outside the supported range")
    return np.float32(exact)


def assert_weather_available(
    *,
    t_available_ms: int,
    t_issue_ms: int,
    t_valid_ms: int | None = None,
    t_observed_ms: int | None = None,
) -> None:
    """Availability cutoff: ``t_available_ms <= t_issue_ms``.

    Valid time and acquisition time are checked only for type. A later valid
    time is allowed when the product was already available at the issue.
    """
    available = _require_int("t_available_ms", t_available_ms)
    issue = _require_int("t_issue_ms", t_issue_ms)
    if t_valid_ms is not None:
        _require_int("t_valid_ms", t_valid_ms)
    if t_observed_ms is not None:
        _require_int("t_observed_ms", t_observed_ms)
    if available > issue:
        raise ValidationError(
            "weather available time is after the forecast issue; "
            "acquisition or valid time does not establish availability",
            code="unavailable",
        )


def format_rust_utc(epoch_ms: int) -> str:
    """Format whole-second UTC. Fractional milliseconds are rejected, not truncated."""
    millis = _require_int("epoch_ms", epoch_ms)
    if millis % 1000 != 0:
        raise UnsupportedMapping(
            "Rust v1 accepts only whole-second YYYY-MM-DDTHH:MM:SSZ timestamps; "
            f"{millis} ms is not representable and was not truncated"
        )
    seconds = millis // 1000
    if seconds < 0:
        raise UnsupportedMapping("Rust v1 timestamps start at 1970-01-01T00:00:00Z")
    stamped = datetime.fromtimestamp(seconds, timezone.utc)
    if stamped.year < 1970:
        raise UnsupportedMapping("Rust v1 timestamps start at 1970-01-01T00:00:00Z")
    return stamped.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_rust_utc_ms(text: str) -> int:
    """Parse the Rust timestamp form back to epoch milliseconds."""
    if not isinstance(text, str) or len(text) != 20 or not text.endswith("Z"):
        raise ValidationError(f"unsupported Rust timestamp {text!r}")
    try:
        stamped = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValidationError(f"unsupported Rust timestamp {text!r}") from exc
    return int(stamped.timestamp() * 1000)


def bind_checkpoint_reference(meta: dict, t_ref_ms: int) -> ReferenceBinding:
    """Attach the application reference time to a Rust checkpoint's origin.

    The checkpoint file is not modified. A later check fails if ``start_utc``
    or the recorded ``t_ref`` changes.
    """
    ref = _require_int("t_ref_ms", t_ref_ms)
    if not isinstance(meta, dict):
        raise ValidationError("checkpoint metadata must be an object")
    if meta.get("schema_version") != 2 or meta.get("model") != "surface_v1":
        raise ValidationError("checkpoint model or schema is not surface_v1 schema 2")
    try:
        start_utc = meta["run_manifest"]["start_utc"]
    except (KeyError, TypeError) as exc:
        raise ValidationError("checkpoint is missing run_manifest.start_utc") from exc
    parse_rust_utc_ms(start_utc)
    return ReferenceBinding(
        t_ref_ms=ref,
        t_start_utc=start_utc,
        model="surface_v1",
        schema_version=2,
    )


def assert_reference_preserved(binding: ReferenceBinding, meta: dict, t_ref_ms: int) -> None:
    restored = bind_checkpoint_reference(meta, t_ref_ms)
    if restored.t_ref_ms != binding.t_ref_ms or t_ref_ms != binding.t_ref_ms:
        raise ValidationError("checkpoint reference time changed", code="reference_time")
    if restored.t_start_utc != binding.t_start_utc:
        raise ValidationError(
            "checkpoint start_utc changed; the Rust time origin is preserved, not rewritten",
            code="reference_time",
        )
    if restored.model != binding.model or restored.schema_version != binding.schema_version:
        raise ValidationError("checkpoint model identity changed", code="reference_time")


def signed_distance_phi(
    grid: AppGrid,
    center_x_m: float,
    center_y_m: float,
    radius_m: float,
) -> np.ndarray:
    """Signed distance in metres, negative inside the disk, application row order.

    Exact zeros are moved to +1e-3 m because Rust rejects a zero level set.
    This is a research ignition disk, not an arrival-time history. Cells with
    negative phi become ``ARRIVAL_INITIAL_UNKNOWN`` in the solver.
    """
    grid = validate_app_grid(grid, allow_research_metric_crs=True)
    radius = _finite("radius_m", radius_m)
    cx = _finite("center_x_m", center_x_m)
    cy = _finite("center_y_m", center_y_m)
    if radius <= 0.0:
        raise ValidationError("radius_m must be positive")
    x, y = app_cell_centers(grid)
    phi = np.hypot(x[None, :] - cx, y[:, None] - cy) - radius
    phi = np.ascontiguousarray(phi, dtype="<f8")
    phi[np.abs(phi) == 0.0] = 1.0e-3
    return phi


def prepare_research_bundle(
    grid: AppGrid,
    *,
    elevation_m: np.ndarray,
    fuel_id: np.ndarray,
    valid: np.ndarray,
    burnable: np.ndarray,
    phi: np.ndarray,
    frames: list[WeatherFrame] | tuple[WeatherFrame, ...],
    origins: TimeOrigins,
    incident_id: str,
    horizons_s: list[float] | tuple[float, ...],
    coverage_end_s: float,
    components_are_midflame: bool,
    fuel_policy: str = "passthrough_catalog_id",
    cfl: float = 0.4,
    max_step_s: float = 30.0,
    wind_limit: str = "effective_0p9_ir",
    ellipse_max_lb: float = 8.0,
    allow_research_metric_crs: bool = False,
) -> ResearchBundle:
    """Convert application-ordered fields into a Rust run bundle in memory.

    Slope, aspect, canopy, temperature, and relative humidity are not
    arguments, so they cannot be dropped silently. Wind is written only when
    the caller states that the components are already midflame.
    """
    rust_grid = app_grid_to_rust(grid, allow_research_metric_crs=allow_research_metric_crs)
    if not incident_id or not isinstance(incident_id, str):
        raise ValidationError("incident_id is required")
    elevation = convert_elevation(elevation_m, grid)
    valid_r = convert_mask(valid, grid, "valid")
    burn_r = convert_mask(burnable, grid, "burnable")
    fuel_r = convert_fuel_ids(
        fuel_id,
        valid,
        burnable,
        grid,
        policy=fuel_policy,
    )
    phi_app = require_spatial_shape(np.asarray(phi, dtype=np.float64), grid, "phi")
    if phi_app.shape != (grid.height, grid.width):
        raise ValidationError("phi must be one 2D field", code="shape_mismatch")
    if not np.isfinite(phi_app).all():
        raise ValidationError("phi must be finite")
    phi_r = np.ascontiguousarray(flip_rows_app_to_rust(phi_app), dtype="<f8")
    _reject_phi_topology(phi_r, valid_r, burn_r, rust_grid)

    if not frames:
        raise ValidationError("at least one weather frame is required")
    issue = _require_int("t_issue_ms", origins.t_issue_ms)
    start = _require_int("t_start_ms", origins.t_start_ms)
    _require_int("t_ref_ms", origins.t_ref_ms)
    start_utc = format_rust_utc(start)

    converted_frames: list[dict[str, np.ndarray]] = []
    times: list[float] = []
    valid_utc: list[str] = []
    available_utc: list[str] = []
    previous = -math.inf
    for index, frame in enumerate(frames):
        time_s = _finite("frame.time_s", frame.time_s)
        if index == 0 and time_s != 0.0:
            raise ValidationError("the first Rust weather frame must be at time_s = 0")
        if time_s <= previous:
            raise ValidationError("weather frame times must be strictly increasing")
        previous = time_s
        assert_weather_available(
            t_available_ms=frame.available_time_ms,
            t_issue_ms=issue,
            t_valid_ms=frame.valid_time_ms,
        )
        # Rust additionally requires availability at the simulation origin.
        if frame.available_time_ms > start:
            raise UnsupportedMapping(
                "Rust v1 requires available_utc <= start_utc. "
                f"frame {index} is available at {frame.available_time_ms} ms, "
                f"after the Rust origin {start} ms. The timestamp was not rewritten."
            )
        east, north = convert_wind_components(
            frame.wind_east_m_s,
            frame.wind_north_m_s,
            grid,
            components_are_midflame=components_are_midflame,
        )
        moisture = convert_moisture_fractions(
            frame.m1, frame.m10, frame.m100, frame.mlh, frame.mlw, grid
        )
        converted_frames.append({"wind_east": east, "wind_north": north, **moisture})
        times.append(time_s)
        valid_utc.append(format_rust_utc(frame.valid_time_ms))
        available_utc.append(format_rust_utc(frame.available_time_ms))

    horizons = tuple(_finite(f"horizon[{i}]", h) for i, h in enumerate(horizons_s))
    if not horizons:
        raise ValidationError("at least one horizon is required")
    previous_h = -1.0
    for horizon in horizons:
        if horizon < 0.0 or horizon != math.floor(horizon):
            raise ValidationError("Rust v1 horizons must be nonnegative integer seconds")
        if horizon <= previous_h:
            raise ValidationError("horizons must be strictly increasing")
        previous_h = horizon
    coverage = _finite("coverage_end_s", coverage_end_s)
    if coverage + 1.0e-12 < horizons[-1]:
        raise ValidationError("environmental coverage ends before the last horizon")
    cfl_v = _finite("cfl", cfl)
    if not (0.0 < cfl_v <= 0.5):
        raise ValidationError("CFL must be in (0, 0.5]")
    step = _finite("max_step_s", max_step_s)
    if step <= 0.0:
        raise ValidationError("max_step_s must be positive")
    lb = _finite("ellipse_max_lb", ellipse_max_lb)
    if not 1.0 <= lb <= 8.0:
        raise ValidationError("ellipse_max_lb must be in [1, 8]")
    if wind_limit not in {"effective_0p9_ir", "none"}:
        raise ValidationError(f"unsupported wind_limit {wind_limit!r}")
    return ResearchBundle(
        grid=rust_grid,
        elevation_m=elevation,
        fuel_id=fuel_r,
        valid=valid_r,
        burnable=burn_r,
        phi=phi_r,
        frames=tuple(converted_frames),
        frame_time_s=tuple(times),
        frame_valid_utc=tuple(valid_utc),
        frame_available_utc=tuple(available_utc),
        start_utc=start_utc,
        incident_id=incident_id,
        horizons_s=horizons,
        coverage_end_s=coverage,
        cfl=cfl_v,
        max_step_s=step,
        wind_limit=wind_limit,
        ellipse_max_lb=lb,
        wind_reference="midflame_horizontal_heading_surface_speed",
    )


def _reject_phi_topology(
    phi: np.ndarray,
    valid: np.ndarray,
    burnable: np.ndarray,
    grid: RustGrid,
) -> None:
    """Fail before launch on inputs Rust rejects as an initial front."""
    modeled = (valid == 1) & (burnable == 1)
    if np.any(modeled & (phi == 0.0)):
        raise ValidationError("exact-zero initial phi is rejected by Rust v1")
    if np.any(~modeled & (phi <= 0.0)):
        raise ValidationError("invalid and non-burnable cells must have positive phi")
    negative = modeled & (phi < 0.0)
    positive = modeled & (phi > 0.0)
    if not negative.any() or not positive.any():
        raise ValidationError("need at least one burned and one unburned modeled cell")
    height, width = grid.height, grid.width
    burned = negative.reshape(height, width)
    # 8-connected companion, matching reject_isolated_ignition.
    padded = np.pad(burned, 1, mode="constant", constant_values=False)
    neighbor = np.zeros_like(burned)
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            neighbor |= padded[1 + dr : 1 + dr + height, 1 + dc : 1 + dc + width]
    isolated = burned & ~neighbor
    if isolated.any():
        raise ValidationError(
            "isolated negative cell is an unresolved point ignition in Rust v1"
        )


def write_research_bundle(directory: Path, bundle: ResearchBundle) -> Path:
    """Write a normalized Rust input directory. Returns the manifest path.

    The destination must not already exist. Array bytes are little-endian
    and row-major, row 0 south.
    """
    directory = Path(directory)
    if directory.exists():
        raise ValidationError(f"bundle destination already exists: {directory}")
    directory.mkdir(parents=True)
    _write_f64(directory / "elevation.f64le", bundle.elevation_m)
    _write_u16(directory / "fuel_id.u16le", bundle.fuel_id)
    _write_u8(directory / "valid.u8", bundle.valid)
    _write_u8(directory / "burnable.u8", bundle.burnable)
    _write_f64(directory / "initial_phi.f64le", bundle.phi)
    catalog_text = FUEL_CATALOG_PATH.read_bytes()
    (directory / "fuel_models.csv").write_bytes(catalog_text)

    frame_meta = []
    for index, (frame, time_s, valid_utc, available_utc) in enumerate(
        zip(
            bundle.frames,
            bundle.frame_time_s,
            bundle.frame_valid_utc,
            bundle.frame_available_utc,
            strict=True,
        )
    ):
        relative = f"weather/frame_{index:03d}"
        frame_dir = directory / relative
        frame_dir.mkdir(parents=True)
        for name in ("wind_east", "wind_north", "m1", "m10", "m100", "mlh", "mlw"):
            _write_f64(frame_dir / f"{name}.f64le", frame[name])
        frame_meta.append(
            {
                "time_s": time_s,
                "observed_or_valid_utc": valid_utc,
                "available_utc": available_utc,
                "directory": relative,
            }
        )

    manifest = {
        "schema_version": 1,
        "incident_id": bundle.incident_id,
        "start_utc": bundle.start_utc,
        "grid": {
            "width": bundle.grid.width,
            "height": bundle.grid.height,
            "cell_size_m": bundle.grid.cell_size_m,
            "x0_m": bundle.grid.x0_m,
            "y0_m": bundle.grid.y0_m,
            "crs": bundle.grid.crs,
        },
        "landscape": {
            "elevation": "elevation.f64le",
            "fuel_id": "fuel_id.u16le",
            "valid": "valid.u8",
            "burnable": "burnable.u8",
            "fuel_catalog": "fuel_models.csv",
        },
        "initial": {
            "phi": "initial_phi.f64le",
            "perimeter_activity": "assume_entire_boundary_active",
        },
        "environment": {
            "wind_reference": bundle.wind_reference,
            "interpolation": "hold_right_continuous",
            "frames": frame_meta,
            "coverage_end_s": bundle.coverage_end_s,
        },
        "simulation": {
            "horizons_s": list(bundle.horizons_s),
            "cfl": bundle.cfl,
            "max_step_s": bundle.max_step_s,
            "wind_limit": bundle.wind_limit,
            "ellipse_max_lb": bundle.ellipse_max_lb,
            "reinitialize": False,
            "solver": "level_set_lf_euler_v1",
        },
    }
    manifest_path = directory / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def _write_f64(path: Path, values: np.ndarray) -> None:
    np.ascontiguousarray(values, dtype="<f8").tofile(path)


def _write_u16(path: Path, values: np.ndarray) -> None:
    np.ascontiguousarray(values, dtype="<u2").tofile(path)


def _write_u8(path: Path, values: np.ndarray) -> None:
    np.ascontiguousarray(values, dtype=np.uint8).tofile(path)
