"""ForecastArrays -> the files the dashboard draws. spec/03 §6, spec/00 §4.2–4.3.

`arrays` is duck-typed: anything with ForecastArrays' `t_q` (5,H,W) and `p_burned_now` (H,W).
`g` is the `grid` dict from scenario.yaml: cell_m, nx, ny, x0, y0 (upper-left), crs.
Everything written is lon/lat; UTM never leaves this module.
"""

from __future__ import annotations

import json
import hashlib
import math
from pathlib import Path
from ember.runtime.cache import _write_atomic

import numpy as np
import shapely
from pyproj import Transformer
from rasterio.features import shapes
from rasterio.transform import Affine
from shapely.geometry import LineString, MultiPolygon, Polygon, mapping, shape
from shapely.geometry.polygon import orient

QUANTILES = (0.1, 0.3, 0.5, 0.7, 0.9)
GRID_CRS = "EPSG:32611"
_TO_LONLAT = Transformer.from_crs(GRID_CRS, "EPSG:4326", always_xy=True)
EMPTY_MPOLY = {"type": "MultiPolygon", "coordinates": []}
EMPTY_MLINE = {"type": "MultiLineString", "coordinates": []}
DANGER_MAX_BYTES = 10_000


def encode_arrival(t_q: np.ndarray, horizon_min: int = 360) -> np.ndarray:
    """00 §4.2; identical to make_fixture.encode_arrival. t_q is minutes after issue."""
    out = np.full(t_q.shape, 255, dtype=np.uint8)
    finite = np.isfinite(t_q)
    burned = finite & (t_q <= 0)
    within = finite & (t_q > 0) & (t_q <= horizon_min)
    out[burned] = 0
    out[within] = np.clip(np.ceil(t_q[within] / 2.0), 1, 254).astype(np.uint8)
    return out


def _check_grid(g: dict) -> None:
    assert g["crs"] == GRID_CRS, f"summarizer expects {GRID_CRS}, got {g['crs']}"


def _mask_utm(mask: np.ndarray, g: dict) -> MultiPolygon:
    """Union of the True cells as a UTM MultiPolygon (empty if none)."""
    mask = np.asarray(mask, dtype=bool)
    if not mask.any():
        return MultiPolygon()
    cell = float(g["cell_m"])
    transform = Affine(cell, 0, float(g["x0"]), 0, -cell, float(g["y0"]))
    geom = shapely.union_all([shape(s) for s, _ in shapes(mask.astype(np.uint8), mask=mask, transform=transform)])
    return _as_multipolygon(geom)


def _as_multipolygon(geom) -> MultiPolygon:
    polys = [p for p in getattr(geom, "geoms", [geom]) if isinstance(p, Polygon) and not p.is_empty]
    return MultiPolygon([orient(p, sign=1.0) for p in polys])


def _to_lonlat(geom):
    def fn(xy: np.ndarray) -> np.ndarray:
        lon, lat = _TO_LONLAT.transform(xy[:, 0], xy[:, 1])
        return np.round(np.column_stack([lon, lat]), 6)

    return shapely.transform(geom, fn)


def _mpoly_geojson(mp_utm: MultiPolygon, simplify_m: float):
    """Simplify in UTM, return the lon/lat shapely MultiPolygon (empty if nothing)."""
    if mp_utm.is_empty:
        return MultiPolygon()
    return _as_multipolygon(_to_lonlat(_as_multipolygon(mp_utm.simplify(simplify_m))))


def _geojson(geom, empty: dict) -> dict:
    return empty if geom.is_empty else json.loads(json.dumps(mapping(geom)))


def mask_to_multipolygon(mask: np.ndarray, g: dict, simplify_m: float) -> dict | None:
    _check_grid(g)
    mp = _mpoly_geojson(_mask_utm(mask, g), simplify_m)
    return None if mp.is_empty else _geojson(mp, EMPTY_MPOLY)


def _lines(geom) -> list[LineString]:
    parts = getattr(geom, "geoms", [geom])
    out: list[LineString] = []
    for p in parts:
        if isinstance(p, LineString) and not p.is_empty:
            out.append(p)
        elif hasattr(p, "geoms"):
            out.extend(_lines(p))
    return out


def _front_utm(burned_mpoly_utm, burnable_unburned_mask: np.ndarray, g: dict) -> list[LineString]:
    if burned_mpoly_utm.is_empty:
        return []
    region = _mask_utm(burnable_unburned_mask, g)
    if region.is_empty:
        return []
    near = region.buffer(float(g["cell_m"]))
    return _lines(shapely.line_merge(burned_mpoly_utm.boundary.intersection(near)))


def _mline_geojson(lines_utm: list[LineString]) -> dict:
    if not lines_utm:
        return dict(EMPTY_MLINE)
    return {"type": "MultiLineString", "coordinates": [mapping(_to_lonlat(ln))["coordinates"] for ln in lines_utm]}


def front_lines(burned_mpoly_utm, burnable_unburned_mask: np.ndarray, g: dict) -> dict:
    """MultiLineString: burned boundary segments within one cell of burnable unburned cells."""
    _check_grid(g)
    return json.loads(json.dumps(_mline_geojson(_front_utm(burned_mpoly_utm, burnable_unburned_mask, g))))


def _dilate(mask: np.ndarray) -> np.ndarray:
    """4-neighbour dilation by one cell."""
    out = mask.copy()
    out[1:] |= mask[:-1]
    out[:-1] |= mask[1:]
    out[:, 1:] |= mask[:, :-1]
    out[:, :-1] |= mask[:, 1:]
    return out


def _fit(geom_utm: MultiPolygon, contain=None):
    """Lon/lat MultiPolygon <= DANGER_MAX_BYTES, simplifying from 30 m and doubling.
    `contain` (lon/lat) is unioned in so lead60 stays inside lead120 after simplification."""
    tol = 30.0
    while True:
        ll = _mpoly_geojson(geom_utm, tol)
        if contain is not None and not contain.is_empty:
            ll = _as_multipolygon(ll.union(contain))
        out = _geojson(ll, EMPTY_MPOLY)
        if len(json.dumps(out)) <= DANGER_MAX_BYTES or ll.is_empty:
            return ll, out
        tol *= 2


def _head_bearing(burned: np.ndarray, t50: np.ndarray, g: dict) -> float | None:
    """Grid bearing (deg cw from grid north) from the burned-cell centroid to the farthest q50 <= 60 cell."""
    with np.errstate(invalid="ignore"):
        ahead = t50 <= 60
    if not burned.any() or not ahead.any():
        return None
    cell = float(g["cell_m"])
    br, bc = np.nonzero(burned)
    cr, cc = br.mean(), bc.mean()
    ar, ac = np.nonzero(ahead)
    dx, dy = (ac - cc) * cell, (cr - ar) * cell  # row 0 = north, so north is -row
    i = int(np.argmax(dx * dx + dy * dy))
    if dx[i] == 0 and dy[i] == 0:
        return None
    # ponytail: grid north, not true north (UTM convergence ~0.3° at Eaton); fine for display.
    return round(math.degrees(math.atan2(dx[i], dy[i])) % 360.0, 2)


def write_assets(
    arrays,
    out_dir: Path,
    rel_prefix: str,
    g: dict,
    burnable: np.ndarray | None = None,
    prev_burned_ha: float | None = None,
    cycle_min: int = 5,
) -> tuple[dict, dict, dict]:
    """Write arrival_q.bin, perimeter.geojson, isochrones.geojson. Returns (assets, danger, stats)."""
    _check_grid(g)
    nx, ny, cell = int(g["nx"]), int(g["ny"]), float(g["cell_m"])
    t_q = np.asarray(arrays.t_q, dtype=np.float32)
    p_now = np.asarray(arrays.p_burned_now, dtype=np.float32)
    assert t_q.shape == (len(QUANTILES), ny, nx), f"t_q shape {t_q.shape} != {(len(QUANTILES), ny, nx)}"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    encoded = np.ascontiguousarray(encode_arrival(t_q))
    raw = encoded.tobytes(order="C")
    assert len(raw) == 5 * ny * nx, f"arrival_q.bin is {len(raw)} bytes, expected {5 * ny * nx}"
    _immutable(out_dir / "arrival_q.bin", raw)

    burned = p_now >= 0.5
    burned_utm = _mask_utm(burned, g)
    burned_ll = _mpoly_geojson(burned_utm, 30.0)
    burnable_mask = np.ones_like(burned) if burnable is None else np.asarray(burnable, dtype=bool)
    # Only the unburned cells next to the burned area can touch its boundary; keeps the vectorizing cheap.
    candidates = burnable_mask & ~burned & _dilate(burned)
    front_utm = _front_utm(burned_utm, candidates, g)
    front = _mline_geojson(front_utm)
    perimeter = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "id": "burned", "properties": {"kind": "burned"}, "geometry": _geojson(burned_ll, EMPTY_MPOLY)},
            {"type": "Feature", "id": "front", "properties": {"kind": "front"}, "geometry": front},
        ],
    }
    _write(out_dir / "perimeter.geojson", perimeter)

    features = []
    with np.errstate(invalid="ignore"):
        for lead in range(60, 361, 60):
            mp = _mask_utm(t_q[2] <= lead, g)
            if mp.is_empty:
                continue
            rings = [_to_lonlat(p.exterior) for p in _as_multipolygon(mp.simplify(30.0)).geoms]
            features.append(
                {
                    "type": "Feature",
                    "properties": {"lead_min": lead, "q": 0.5},
                    "geometry": {"type": "MultiLineString", "coordinates": [mapping(r)["coordinates"] for r in rings]},
                }
            )
        p60_utm = _mask_utm(t_q[1] <= 60, g)
        p120_utm = _mask_utm(t_q[1] <= 120, g)
    _write(out_dir / "isochrones.geojson", {"type": "FeatureCollection", "features": json.loads(json.dumps(features))})

    p60_ll, lead60 = _fit(p60_utm)
    _, lead120 = _fit(p120_utm, contain=p60_ll)
    danger = {"lead60_p30": lead60, "lead120_p30": lead120}

    burned_ha = round(float(p_now.sum()) * cell * cell / 1e4, 4)
    growth = 0.0 if prev_burned_ha is None else round((burned_ha - prev_burned_ha) * 60 / cycle_min, 4)
    stats = {
        "burned_ha": burned_ha,
        "front_km": round(sum(ln.length for ln in front_utm) / 1000, 4),
        "growth_ha_per_h": growth,
        "head_bearing_deg": _head_bearing(burned, t_q[2], g),
    }
    assets = {
        "arrival_q": f"{rel_prefix}/arrival_q.bin",
        "perimeter": f"{rel_prefix}/perimeter.geojson",
        "isochrones": f"{rel_prefix}/isochrones.geojson",
    }
    receipt = {name: {"bytes": (out_dir / name).stat().st_size,
                      "sha256": hashlib.sha256((out_dir / name).read_bytes()).hexdigest()}
               for name in ("arrival_q.bin", "perimeter.geojson", "isochrones.geojson")}
    _write(out_dir / "receipt.json", receipt)
    verify_assets(out_dir, 5 * ny * nx)
    return assets, danger, stats


def _write(path: Path, obj: object) -> None:
    _immutable(path, json.dumps(obj, separators=(",", ":")).encode("utf-8"))


def _immutable(path: Path, data: bytes) -> None:
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(f"Refusing to replace immutable forecast asset {path.name}")
        return
    _write_atomic(path, data)


def verify_assets(directory: Path, expected_arrival_bytes: int) -> None:
    """Receipt is internal metadata, not a change to frozen Cycle.assets."""
    receipt = json.loads((directory / "receipt.json").read_text())
    if set(receipt) != {"arrival_q.bin", "perimeter.geojson", "isochrones.geojson"}:
        raise ValueError("Incomplete asset receipt")
    for name, expected in receipt.items():
        raw = (directory / name).read_bytes()
        if len(raw) != expected["bytes"] or hashlib.sha256(raw).hexdigest() != expected["sha256"]:
            raise ValueError(f"Asset integrity mismatch: {name}")
    if receipt["arrival_q.bin"]["bytes"] != expected_arrival_bytes:
        raise ValueError("Arrival raster size mismatch")
