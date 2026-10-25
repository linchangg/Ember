"""Area X: the honest-number region and the ONE satellite hit definition (01 §2, §6; 03 §9).

`scripts/check_goes.py` (G1-a) and `risk/metrics.py` both import `area_x_satellite_hit`
and `t_sat_goes` from here, so the gate and the reported number share one definition.
"""

from __future__ import annotations

from functools import lru_cache

import shapely
from pyproj import Transformer
from shapely.geometry import Polygon, box

from emberline.contracts import GOES_STRONG

HIT_AREA_FRAC = 0.5  # >= 50% of the footprint inside area X
UTM = "EPSG:32611"


@lru_cache(maxsize=1)
def _to_utm() -> Transformer:
    return Transformer.from_crs("EPSG:4326", UTM, always_xy=True)


def _utm(geom):
    return shapely.transform(geom, _to_utm().transform, interleaved=False)


def area_x_box(cfg: dict) -> tuple[float, float, float, float]:
    a = cfg["area_x"]
    return a["lon_min"], a["lat_min"], a["lon_max"], a["lat_max"]


def area_x_polygon(cfg: dict) -> Polygon:
    return box(*area_x_box(cfg))


def lake_ave_lon(cfg: dict) -> float:
    # ponytail: Lake Ave as a constant meridian; T9 writes the real centreline (lake_ave.geojson)
    return cfg["area_x"]["lon_max"]


def pixel_is_hit(px: dict, cfg: dict) -> bool:
    """Strong mask, >= 50% of footprint area inside area X (EPSG:32611), wholly west of Lake Ave."""
    if px["mask"] not in GOES_STRONG:
        return False
    ring = [tuple(p) for p in px["ring"]]
    lake = lake_ave_lon(cfg)
    if not all(lon < lake for lon, _ in ring):
        return False
    foot = _utm(Polygon(ring))
    inside = foot.intersection(_utm(area_x_polygon(cfg))).area
    return foot.area > 0 and inside / foot.area >= HIT_AREA_FRAC


def area_x_satellite_hit(pixels: list[dict], cfg: dict) -> bool:
    return any(pixel_is_hit(px, cfg) for px in pixels)


def t_sat_goes(scans: list[tuple[int, bool]]) -> int | None:
    """First hit scan i with >= 2 hits among scans i, i+1, i+2 (fewer at the end). `scans` sorted by time."""
    hits = [h for _, h in scans]
    for i, (t, h) in enumerate(scans):
        if h and sum(hits[i : i + 3]) >= 2:
            return t
    return None
