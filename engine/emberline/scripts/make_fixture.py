"""Analytic fixture. spec/00_contracts.md §10. numpy, shapely, and Pillow only.

The fire is an ellipse (head 1.2 km/h, length:width 3, heading 240°) grown from
the Eaton ignition. Fifty seeded members (spread rate ~±18%, heading mostly ±8° with
a 20% wind-shift tail to ±25°, ignition point ±250 m) supply the quantiles, so some
members overlap closely and a few burn less likely areas. FIX-Z2 is placed so rule R1 first holds at cycle 20, and FIX-Z3 so
rule R2 first holds at cycle 30 (spec/00 §7, spec/03 §7–8).
"""

from __future__ import annotations

import json
import math
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from PIL import Image
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient

from emberline.contracts import LEADS_MIN, SCHEMA_VERSION, CycleBundle, lead_idx

REPO = Path(__file__).resolve().parents[3]
FIXTURE_STATIC = REPO / "fixtures" / "static" / "fixture"
FIXTURE_MAIN = REPO / "fixtures" / "cache" / "fixture" / "main"

# Eaton outer extent (spec/00 §2) on a 120×120 grid at 255 m (same 30.6 km box).
X0 = 381_480.0
Y0 = 3_800_280.0
CELL = 255.0
NX = NY = 120
WIDTH = NX * CELL
HEIGHT = NY * CELL
Y_SOUTH = Y0 - HEIGHT

IGN_LON = -118.088
IGN_LAT = 34.205
HEADING = 240.0
ROS_M_PER_MIN = 1.2 * 1000 / 60  # 20 m/min
LB = 3.0
ECC = math.sqrt(1 - (1 / LB) ** 2)
HORIZON_MIN = 360
CYCLE_MIN = 5
N_CYCLES = 36
N_MEMBERS = 50
EVAC_MIN = 45.0
SIZE_M = 1500.0
# January is PST (UTC−8). spec/00 §1. A fixed offset avoids the tzdata package on Windows.
PST = timezone(timedelta(hours=-8))

# spec/00 §7, copied here so the fixture does not take a YAML dependency.
R1_TEXT = "P(fire reaches zone within 2 h) ≥ 30%"
R2_TEXT = "P(fire reaches zone within 1 h) ≥ 50%, or act-by time within 15 min"
P_WORDS = ((0.9, "very likely"), (0.7, "likely"), (0.5, "more likely than not"), (0.3, "possible"))

_A = 6_378_137.0
_F = 1 / 298.257223563
_E2 = _F * (2 - _F)
_EP2 = _E2 / (1 - _E2)
_K0 = 0.9996
_LON0 = math.radians(-117.0)  # UTM zone 11
_FALSE_E = 500_000.0

def _make_members() -> tuple[tuple[float, float, float, float], ...]:
    """(ros m/min, heading deg, ignition shift east m, north m); seeded, so the fixture is reproducible."""
    rng = np.random.default_rng(20250108)
    scale = np.clip(np.exp(rng.normal(0.0, 0.18, N_MEMBERS)), 0.6, 1.5)
    wide = rng.random(N_MEMBERS) < 0.2
    offset = np.clip(np.where(wide, rng.normal(0.0, 25.0, N_MEMBERS), rng.normal(0.0, 8.0, N_MEMBERS)), -55.0, 55.0)
    shift = rng.normal(0.0, 250.0, (N_MEMBERS, 2))
    return tuple(
        (float(ROS_M_PER_MIN * scale[i]), float(HEADING + offset[i]), float(shift[i, 0]), float(shift[i, 1]))
        for i in range(N_MEMBERS)
    )


MEMBERS = _make_members()

IGNITION = datetime(2025, 1, 8, 2, 15, tzinfo=timezone.utc)
CREATED_WALL_MS = int(datetime(2026, 10, 24, 19, 0, tzinfo=timezone.utc).timestamp() * 1000)


def epoch_ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


IGN_MS = epoch_ms(IGNITION)


def utm_forward(lon_deg: float, lat_deg: float) -> tuple[float, float]:
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    tan_lat = math.tan(lat)
    n = _A / math.sqrt(1 - _E2 * sin_lat * sin_lat)
    t = tan_lat * tan_lat
    c = _EP2 * cos_lat * cos_lat
    aa = (lon - _LON0) * cos_lat
    e2, e4, e6 = _E2, _E2 * _E2, _E2**3
    m = _A * (
        (1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * lat
        - (3 * e2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * math.sin(2 * lat)
        + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * lat)
        - (35 * e6 / 3072) * math.sin(6 * lat)
    )
    easting = _FALSE_E + _K0 * n * (
        aa
        + (1 - t + c) * aa**3 / 6
        + (5 - 18 * t + t * t + 72 * c - 58 * _EP2) * aa**5 / 120
    )
    northing = _K0 * (
        m
        + n
        * tan_lat
        * (
            aa * aa / 2
            + (5 - t + 9 * c + 4 * c * c) * aa**4 / 24
            + (61 - 58 * t + t * t + 600 * c - 330 * _EP2) * aa**6 / 720
        )
    )
    return easting, northing


def utm_inverse(easting: float, northing: float) -> tuple[float, float]:
    x = easting - _FALSE_E
    y = northing
    e2 = _E2
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    mu = y / (_K0 * _A * (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256))
    lat1 = (
        mu
        + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu)
        + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
        + (151 * e1**3 / 96) * math.sin(6 * mu)
        + (1097 * e1**4 / 512) * math.sin(8 * mu)
    )
    sin_lat = math.sin(lat1)
    cos_lat = math.cos(lat1)
    tan_lat = math.tan(lat1)
    n1 = _A / math.sqrt(1 - e2 * sin_lat * sin_lat)
    r1 = _A * (1 - e2) / (1 - e2 * sin_lat * sin_lat) ** 1.5
    t1 = tan_lat * tan_lat
    c1 = _EP2 * cos_lat * cos_lat
    d = x / (n1 * _K0)
    lat = lat1 - (n1 * tan_lat / r1) * (
        d**2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1 * c1 - 9 * _EP2) * d**4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1 * t1 - 252 * _EP2 - 3 * c1 * c1) * d**6 / 720
    )
    lon = _LON0 + (
        d
        - (1 + 2 * t1 + c1) * d**3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1 * c1 + 8 * _EP2 + 24 * t1 * t1) * d**5 / 120
    ) / cos_lat
    return math.degrees(lon), math.degrees(lat)


def along_cross_offset(along_m: np.ndarray | float, cross_m: np.ndarray | float) -> tuple[np.ndarray, np.ndarray]:
    """Metres east/north of the ignition, heading 240°, cross positive to the right."""
    th = math.radians(HEADING)
    forward_e, forward_n = math.sin(th), math.cos(th)
    right_e, right_n = math.cos(th), -math.sin(th)
    return along_m * forward_e + cross_m * right_e, along_m * forward_n + cross_m * right_n


def arrival_minutes(
    de: np.ndarray | float,
    dn: np.ndarray | float,
    heading_deg: float,
    ros_m_per_min: float,
) -> np.ndarray:
    """Minutes after ignition when the member's ellipse reaches each point."""
    th = math.radians(heading_deg)
    forward_e, forward_n = math.sin(th), math.cos(th)
    right_e, right_n = math.cos(th), -math.sin(th)
    s = de * forward_e + dn * forward_n
    r = de * right_e + dn * right_n
    ka = ros_m_per_min / (1 + ECC)
    kc = ECC * ka
    a_coef = 1.0 / (LB * LB)
    b_coef = 2 * s * kc / (ka * ka)
    c_coef = -(s * s + (LB * LB) * r * r) / (ka * ka)
    disc = np.maximum(b_coef * b_coef - 4 * a_coef * c_coef, 0.0)
    return (-b_coef + np.sqrt(disc)) / (2 * a_coef)


def quantile_eta(etas: np.ndarray, q: float) -> float:
    """Equal-weight quantile: first sorted member whose cumulative weight reaches q."""
    ordered = np.sort(etas)
    return float(ordered[math.ceil(q * N_MEMBERS) - 1])


def p_by_lead(etas: np.ndarray, k_min: float) -> np.ndarray:
    leads = np.asarray(LEADS_MIN, dtype=float)
    count = np.sum(etas[:, None] <= (k_min + leads)[None, :], axis=0)
    return count / N_MEMBERS


def act_by_ms(etas: np.ndarray, k_ms: int) -> int | None:
    """q30 arrival − evac in whole epoch ms, exactly as zone_risk rows store it (None past the horizon)."""
    q30 = quantile_eta(etas, 0.3)
    if q30 > (k_ms - IGN_MS) / 60_000 + HORIZON_MIN:
        return None
    return IGN_MS + int(round(q30 * 60_000)) - int(EVAC_MIN * 60_000)


def rule_holds(etas: np.ndarray, k_ms: int, rule: str) -> bool:
    """spec/00 §7 on the same integer-ms act-by that risk/alerts.py reads from the bundle."""
    p = p_by_lead(etas, (k_ms - IGN_MS) / 60_000)
    if rule == "R1":
        return bool(p[lead_idx(120)] >= 0.30)
    act = act_by_ms(etas, k_ms)
    return bool(p[lead_idx(60)] >= 0.50 or (act is not None and act - k_ms <= 15 * 60_000))


def first_firing_cycle(etas: np.ndarray, rule: str) -> int:
    for n in range(1, 80):
        if rule_holds(etas, IGN_MS + n * CYCLE_MIN * 60_000, rule):
            return n
    return 99


def encode_arrival(t_after_min: np.ndarray) -> np.ndarray:
    """spec/00 §4.2. t_after is minutes after issue; <=0 is already burned."""
    out = np.full(t_after_min.shape, 255, dtype=np.uint8)
    finite = np.isfinite(t_after_min)
    burned = finite & (t_after_min <= 0)
    within = finite & (t_after_min > 0) & (t_after_min <= HORIZON_MIN)
    out[burned] = 0
    coded = np.ceil(t_after_min[within] / 2.0)
    out[within] = np.clip(coded, 1, 254).astype(np.uint8)
    return out


def write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(obj, indent=2) + "\n").encode("utf-8"))


def pst(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, PST)


def hm(ms: int) -> str:
    return pst(ms).strftime("%H:%M")


def spoken(ms: int) -> str:
    moment = pst(ms)
    hour = str(int(moment.strftime("%I")))
    return f"{hour}:{moment.strftime('%M %p')}"


def p_words(p: float) -> str:
    for threshold, words in P_WORDS:
        if p >= threshold:
            return words
    return "unlikely"


def boundary_rings(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    """Clockwise cell-corner rings of True cells, in (col, row) with row 0 = north."""
    h, w = mask.shape
    segs: list[tuple[tuple[int, int], tuple[int, int]]] = []
    for r in range(h):
        for c in range(w):
            if not mask[r, c]:
                continue
            if r == 0 or not mask[r - 1, c]:
                segs.append(((c, r), (c + 1, r)))
            if c == w - 1 or not mask[r, c + 1]:
                segs.append(((c + 1, r), (c + 1, r + 1)))
            if r == h - 1 or not mask[r + 1, c]:
                segs.append(((c + 1, r + 1), (c, r + 1)))
            if c == 0 or not mask[r, c - 1]:
                segs.append(((c, r + 1), (c, r)))
    nxt: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for start, end in segs:
        nxt.setdefault(start, []).append(end)
    unused = set(segs)
    rings: list[list[tuple[int, int]]] = []
    for start, end in segs:
        if (start, end) not in unused:
            continue
        ring = [start]
        cur = end
        unused.remove((start, end))
        while cur != start:
            ring.append(cur)
            step = next((pt for pt in nxt[cur] if (cur, pt) in unused), None)
            if step is None:
                break
            unused.remove((cur, step))
            cur = step
        ring.append(start)
        if len(ring) >= 4:
            rings.append(ring)
    return rings


def _utm_corner(col: int, row: int) -> tuple[float, float]:
    return X0 + col * CELL, Y0 - row * CELL


def vectorize_mask(mask: np.ndarray) -> tuple[float, dict, dict]:
    """Boundary length in km, a MultiPolygon, and a MultiLineString. Both lon/lat."""
    polygons: list[list[list[list[float]]]] = []
    lines: list[list[list[float]]] = []
    length_m = 0.0
    for ring in boundary_rings(mask):
        utm = [_utm_corner(c, r) for c, r in ring]
        poly = Polygon(utm)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty or poly.geom_type != "Polygon":
            continue
        length_m += poly.exterior.length
        if len(poly.exterior.coords) > 24:
            poly = poly.simplify(150, preserve_topology=True)
        lonlat = [utm_inverse(e, n) for e, n in poly.exterior.coords]
        shaped = orient(Polygon(lonlat), sign=1.0)
        coords = [[round(lon, 6), round(lat, 6)] for lon, lat in shaped.exterior.coords]
        if coords[0] != coords[-1]:
            coords.append(coords[0])
        polygons.append([coords])
        lines.append(coords)
    multi = {"type": "MultiPolygon", "coordinates": polygons}
    front = {"type": "MultiLineString", "coordinates": lines}
    return length_m / 1000, multi, front


def rectangle_utm(center_along: float, center_cross: float, size: float = SIZE_M) -> list[tuple[float, float]]:
    half = size / 2
    corners = (
        (center_along - half, center_cross - half),
        (center_along - half, center_cross + half),
        (center_along + half, center_cross + half),
        (center_along + half, center_cross - half),
    )
    ign_e, ign_n = utm_forward(IGN_LON, IGN_LAT)
    pts = []
    for along, cross in corners:
        de, dn = along_cross_offset(along, cross)
        pts.append((ign_e + float(de), ign_n + float(dn)))
    return pts


def ring_lonlat(utm_pts: list[tuple[float, float]]) -> list[list[float]]:
    lonlat = [utm_inverse(e, n) for e, n in utm_pts]
    shaped = orient(Polygon(lonlat), sign=1.0)
    coords = [[round(lon, 6), round(lat, 6)] for lon, lat in shaped.exterior.coords]
    if coords[0] != coords[-1]:
        coords.append(coords[0])
    return coords


def center_lonlat(center_along: float, center_cross: float) -> tuple[float, float]:
    ign_e, ign_n = utm_forward(IGN_LON, IGN_LAT)
    de, dn = along_cross_offset(center_along, center_cross)
    return utm_inverse(ign_e + float(de), ign_n + float(dn))


def member_etas(center_along: float, center_cross: float, size: float = SIZE_M) -> np.ndarray:
    """Earliest arrival (minutes after ignition) of each member anywhere in the rectangle."""
    half = size / 2
    alongs = np.arange(-half, half + 1, 25.0) + center_along
    crosses = np.arange(-half, half + 1, 25.0) + center_cross
    aa, cc = np.meshgrid(alongs, crosses)
    de, dn = along_cross_offset(aa, cc)
    flat_de, flat_dn = de.ravel(), dn.ravel()
    etas = np.empty(N_MEMBERS)
    for i, (ros, heading, sx, sy) in enumerate(MEMBERS):
        etas[i] = float(np.min(arrival_minutes(flat_de - sx, flat_dn - sy, heading, ros)))
    return etas


def inside_grid(center_along: float, center_cross: float, size: float = SIZE_M) -> bool:
    for east, north in rectangle_utm(center_along, center_cross, size):
        if not (X0 <= east <= X0 + WIDTH and Y_SOUTH <= north <= Y0):
            return False
    return True


def polygons_overlap(a: tuple[float, float], b: tuple[float, float], size: float = SIZE_M) -> bool:
    pa = Polygon(rectangle_utm(a[0], a[1], size))
    pb = Polygon(rectangle_utm(b[0], b[1], size))
    return pa.intersects(pb) and not pa.touches(pb)


@dataclass(frozen=True)
class Zone:
    zone_id: str
    name: str
    center_along: float
    center_cross: float
    population: int
    housing: int
    n_critical: int
    tier: int
    egress_roads: int
    etas: np.ndarray


def first_along_reaching(cross: float, rule: str, goal: int) -> float:
    """Smallest along distance (to bisection precision) where the rule first fires at cycle >= goal."""
    lo, hi = 300.0, 20_000.0
    for _ in range(50):
        mid = (lo + hi) / 2
        if first_firing_cycle(member_etas(mid, cross), rule) < goal:
            lo = mid
        else:
            hi = mid
    return hi


def solve_along(cross: float, rule: str, goal: int) -> float:
    """Middle of the band of along distances that fire first at `goal`, so no rule sits on a cycle boundary."""
    near = first_along_reaching(cross, rule, goal)
    far = first_along_reaching(cross, rule, goal + 1)
    along = (near + far) / 2
    cycle = first_firing_cycle(member_etas(along, cross), rule)
    if cycle != goal or far - near < 20:
        probe = [(d, first_firing_cycle(member_etas(d, cross), rule)) for d in range(1000, 16000, 500)]
        raise RuntimeError(f"{rule} did not land on cycle {goal} at cross {cross}: got {cycle}, band "
                           f"{near:.1f}-{far:.1f} m, probe {probe}")
    return along


def place_zones() -> list[Zone]:
    z3_along = solve_along(-800.0, "R2", 30)
    z2_along = solve_along(800.0, "R1", 20)
    if not inside_grid(z3_along, -800.0) or not inside_grid(z2_along, 800.0):
        raise RuntimeError(f"alert zones fall outside the grid: Z3 {z3_along}, Z2 {z2_along}")
    if polygons_overlap((z3_along, -800.0), (z2_along, 800.0)):
        raise RuntimeError("FIX-Z2 and FIX-Z3 overlap")

    far_start = max(z2_along, z3_along) + SIZE_M + 200
    far_ids = ("FIX-Z1", "FIX-Z4", "FIX-Z5", "FIX-Z6")
    placed: list[tuple[str, float, float]] = [("FIX-Z3", z3_along, -800.0), ("FIX-Z2", z2_along, 800.0)]
    along = far_start
    for zone_id in far_ids:
        found: tuple[float, float] | None = None
        cursor = along
        for _ in range(30):
            for cross in (0.0, 1800.0, -1800.0, 2700.0, -2700.0):
                spot = (cursor, cross)
                if not inside_grid(cursor, cross):
                    continue
                if any(polygons_overlap(spot, (a, c)) for _, a, c in placed):
                    continue
                etas = member_etas(cursor, cross)
                if first_firing_cycle(etas, "R1") <= N_CYCLES or first_firing_cycle(etas, "R2") <= N_CYCLES:
                    continue
                found = spot
                break
            if found:
                break
            cursor += 400
        if found is None:
            raise RuntimeError(f"no quiet spot in the grid for {zone_id}")
        placed.append((zone_id, found[0], found[1]))
        along = found[0] + SIZE_M + 200

    meta = {
        "FIX-Z2": ("Fixture Z2", 420, 150, 0, 3, 2),
        "FIX-Z3": ("Fixture Z3", 860, 300, 1, 2, 3),
        "FIX-Z1": ("Fixture Z1", 180, 70, 0, 3, 2),
        "FIX-Z4": ("Fixture Z4", 180, 70, 0, 3, 2),
        "FIX-Z5": ("Fixture Z5", 180, 70, 0, 3, 2),
        "FIX-Z6": ("Fixture Z6", 180, 70, 0, 3, 2),
    }
    zones = []
    for zone_id, center_along, center_cross in placed:
        name, pop, housing, n_crit, tier, egress = meta[zone_id]
        zones.append(
            Zone(zone_id, name, center_along, center_cross, pop, housing, n_crit, tier, egress, member_etas(center_along, center_cross))
        )
    by_id = {z.zone_id: z for z in zones}
    if first_firing_cycle(by_id["FIX-Z2"].etas, "R1") != 20:
        raise RuntimeError("FIX-Z2 does not cross R1 at cycle 20")
    if first_firing_cycle(by_id["FIX-Z3"].etas, "R2") != 30:
        raise RuntimeError("FIX-Z3 does not cross R2 at cycle 30")
    return zones


def grid_arrivals() -> np.ndarray:
    """Quantile arrival minutes after ignition, shape (5, ny, nx), row 0 = north."""
    ign_e, ign_n = utm_forward(IGN_LON, IGN_LAT)
    cols = (np.arange(NX) + 0.5) * CELL
    rows = (np.arange(NY) + 0.5) * CELL
    east = X0 + cols
    north = Y0 - rows
    ee, nn = np.meshgrid(east, north)
    de = ee - ign_e
    dn = nn - ign_n
    stacked = np.empty((N_MEMBERS, NY, NX))
    for i, (ros, heading, sx, sy) in enumerate(MEMBERS):
        stacked[i] = arrival_minutes(de - sx, dn - sy, heading, ros)
    ordered = np.sort(stacked, axis=0)
    idxs = [math.ceil(q * N_MEMBERS) - 1 for q in (0.1, 0.3, 0.5, 0.7, 0.9)]
    return ordered[idxs]


def pixel_ring(east: float, north: float) -> list[list[float]]:
    half_e, half_n = 2300 / 2, 2800 / 2
    return ring_lonlat(
        [
            (east - half_e, north - half_n),
            (east + half_e, north - half_n),
            (east + half_e, north + half_n),
            (east - half_e, north + half_n),
        ]
    )


def front_centers(issue_age_min: float, count: int) -> list[tuple[float, float]]:
    ign_e, ign_n = utm_forward(IGN_LON, IGN_LAT)
    dist = ROS_M_PER_MIN * issue_age_min
    offsets = (-20.0, 0.0, 20.0, -40.0)[:count]
    pts = []
    for offset in offsets:
        de, dn = along_cross_offset_heading(dist, HEADING + offset)
        pts.append((ign_e + de, ign_n + dn))
    return pts


def along_cross_offset_heading(dist_m: float, heading_deg: float) -> tuple[float, float]:
    th = math.radians(heading_deg)
    return dist_m * math.sin(th), dist_m * math.cos(th)


def alert_copy(zone: Zone, rule: str, k_ms: int, cycle_id: str, p120: float, p60: float) -> dict:
    if rule == "R1":
        rule_id, level, level_token, level_words, lead_h, p = (
            "R1_WARNING",
            "warning",
            "WARNING",
            "a warning",
            2,
            p120,
        )
        rule_text = R1_TEXT
    else:
        rule_id, level, level_token, level_words, lead_h, p = (
            "R2_ORDER",
            "order_recommended",
            "EVACUATION ORDER",
            "an evacuation order",
            1,
            p60,
        )
        rule_text = R2_TEXT
    act_ms = act_by_ms(zone.etas, k_ms)
    act_hm = hm(act_ms) if act_ms is not None else "unknown"
    act_spoken = spoken(act_ms) if act_ms is not None else "an unknown time"
    critical = ", school" if zone.n_critical else ""
    critical_spoken = ", including a school" if zone.n_critical else ""
    lead_words = "1 hour" if lead_h == 1 else "2 hours"
    text = (
        f"{level_token} recommended — {zone.zone_id}: "
        f"fire {p_words(p)} within {lead_h} h (P {p:.0%}). Act by {act_hm} PST. "
        f"~{zone.population:,} residents{critical}. Forecast {hm(k_ms)}, satellite data through {hm(k_ms)}."
    )
    voice = (
        "This is Emberline. Briefing for the duty officer. "
        f"Recommend {level_words} for {zone.name}. Fire is {p_words(p)} to reach the zone within {lead_words}. "
        f"Act by {act_spoken}. About {zone.population} residents{critical_spoken}. "
        "This is decision support; you make the call. Say 'acknowledged' to confirm."
    )
    if len(text) > 300 or len(voice.split()) > 110:
        raise RuntimeError(f"template too long: text {len(text)} voice {len(voice.split())}")
    return {
        "alert_id": f"al_main_{zone.zone_id}_{rule_id}_{k_ms}",
        "scenario_id": "fixture",
        "branch_id": "main",
        "zone_ids": [zone.zone_id],
        "level": level,
        "rule_id": rule_id,
        "rule_text": rule_text,
        "cycle_id": cycle_id,
        "triggered_sim_ms": k_ms,
        "act_by_sim_ms": act_ms,
        "state": "proposed",
        "text": text,
        "voice_script": voice,
        "draft_source": "template",
        "briefing_id": None,
        "decided_by": None,
        "decided_wall_ms": None,
        "call_id": None,
        "call_status": None,
        "acked_via": None,
        "acked_wall_ms": None,
        "acked_sim_ms": None,
    }


def zone_risk_row(zone: Zone, cycle_id: str, k_ms: int, prev_p120: dict[str, float], open_orders: set[str]) -> dict:
    k_min = (k_ms - IGN_MS) / 60_000
    p = p_by_lead(zone.etas, k_min)
    p120 = float(p[lead_idx(120)])
    if zone.zone_id in open_orders or float(p[lead_idx(60)]) >= 0.5:
        status = "order"
    elif p120 >= 0.3:
        status = "warning"
    elif float(p[lead_idx(360)]) >= 0.1:
        status = "watch"
    else:
        status = "clear"
    if zone.zone_id not in prev_p120:
        trend = "flat"
    else:
        delta = p120 - prev_p120[zone.zone_id]
        trend = "up" if delta > 0.05 else "down" if delta < -0.05 else "flat"
    prev_p120[zone.zone_id] = p120
    mult = {1: 2.0, 2: 1.3, 3: 1.0}[zone.tier]
    score = p120 * (1 + math.log10(1 + zone.population)) * mult
    drivers = [f"P {p120 * 100:.0f}% by +2h"]
    if zone.n_critical:
        drivers.append("school")
    if zone.egress_roads <= 1:
        drivers.append(f"{zone.egress_roads} egress road")

    def eta_field(q: float) -> int | None:
        t = quantile_eta(zone.etas, q)
        if t > k_min + HORIZON_MIN:
            return None
        return IGN_MS + int(round(t * 60_000))

    act = act_by_ms(zone.etas, k_ms)
    return {
        "cycle_id": cycle_id,
        "scenario_id": "fixture",
        "branch_id": "main",
        "zone_id": zone.zone_id,
        "knowledge_sim_ms": k_ms,
        "p_by_lead": [round(float(v), 6) for v in p],
        "eta_q10_sim_ms": eta_field(0.1),
        "eta_q50_sim_ms": eta_field(0.5),
        "eta_q90_sim_ms": eta_field(0.9),
        "act_by_sim_ms": act,
        "act_by_basis": f"q30 arrival − {EVAC_MIN:g} min evac",
        "evac_min": EVAC_MIN,
        "population": zone.population,
        "housing": zone.housing,
        "n_critical": zone.n_critical,
        "tier": zone.tier,
        "risk_score": round(score, 6),
        "rank": 0,
        "status": status,
        "trend": trend,
        "drivers": drivers[:3],
    }


def rank_zones(rows: list[dict]) -> None:
    severity = {"order": 3, "warning": 2, "watch": 1, "clear": 0}
    rows.sort(
        key=lambda row: (
            -severity[row["status"]],
            row["act_by_sim_ms"] if row["act_by_sim_ms"] is not None else 10**18,
            -row["risk_score"],
        )
    )
    for i, row in enumerate(rows, start=1):
        row["rank"] = i


def propose(zones: list[Zone], k_ms: int, cycle_id: str, history: list[dict]) -> list[dict]:
    active = {"proposed", "approved", "calling", "called", "acknowledged"}
    new: list[dict] = []
    transitions: list[dict] = []

    def blocked(zone_id: str, level: str) -> bool:
        for prev in history:
            if prev["zone_ids"][0] != zone_id:
                continue
            same_level = prev["level"] == level
            order_covers = level == "warning" and prev["level"] == "order_recommended" and prev["state"] in active
            if not same_level and not order_covers:
                continue
            if prev["state"] in active:
                return True
            if same_level and k_ms - prev["triggered_sim_ms"] < 30 * 60_000:
                return True
        return False

    k_min = (k_ms - IGN_MS) / 60_000
    for zone in zones:
        if zone.population < 1:
            continue
        p = p_by_lead(zone.etas, k_min)
        p120, p60 = float(p[lead_idx(120)]), float(p[lead_idx(60)])
        fired = []
        if rule_holds(zone.etas, k_ms, "R1"):
            fired.append("R1")
        if rule_holds(zone.etas, k_ms, "R2"):
            fired.append("R2")
        for rule in fired:
            level = "warning" if rule == "R1" else "order_recommended"
            if blocked(zone.zone_id, level):
                continue
            alert = alert_copy(zone, rule, k_ms, cycle_id, p120, p60)
            new.append(alert)
            history.append(alert)
            if level != "order_recommended":
                continue
            for prev in history:
                if prev is alert:
                    continue
                if prev["zone_ids"][0] == zone.zone_id and prev["level"] == "warning" and prev["state"] == "proposed":
                    prev["state"] = "superseded"
                    if prev not in new and prev not in transitions:
                        transitions.append(prev)
    return [a for a in new if a["state"] == "proposed"] + transitions


def scan_and_obs(k_ms: int, cycle_id: str, n_cycle: int) -> tuple[dict, list[dict]]:
    stamp = datetime.fromtimestamp(k_ms / 1000, timezone.utc).strftime("%Y%m%dT%H%M%S")
    scan_id = f"goes18_{stamp}"
    n_hot = 2 + (n_cycle - 1) % 3
    n_clear = 90
    scan = {
        "scan_id": scan_id,
        "scenario_id": "fixture",
        "branch_id": "main",
        "sensor_id": "goes18_fdcc",
        "mode": "CONUS",
        "t_start_sim_ms": k_ms,
        "t_end_sim_ms": k_ms,
        "available_sim_ms": k_ms,
        "n_pixels_aoi": n_hot + n_clear,
        "n_hot": n_hot,
        "n_clear": n_clear,
        "assimilated": True,
        "pixels_path": None,
        "provenance": "simulated",
    }
    ign_age = (k_ms - IGN_MS) / 60_000
    obs = []
    for j, (east, north) in enumerate(front_centers(ign_age, n_hot)):
        px, py = 100 + n_cycle, 200 + j
        obs.append(
            {
                "obs_id": f"obs_goes18_fdcc_{scan_id}_{px}_{py}",
                "scenario_id": "fixture",
                "branch_id": "main",
                "sensor_id": "goes18_fdcc",
                "kind": "goes_pixel",
                "observed_sim_ms": k_ms,
                "available_sim_ms": k_ms,
                "geometry": {"type": "footprint", "ring": pixel_ring(east, north)},
                "hot": True,
                "footprint_key": f"goes18:{px}:{py}",
                "fdc_class": 10,
                "report_kind": None,
                "confidence": 1.0,
                "frp_mw": None,
                "trust": 1.0,
                "provenance": "simulated",
                "source_ref": "fixture",
                "status": "assimilated",
                "cycle_id": cycle_id,
                "influence": None,
            }
        )
    return scan, obs


def sensors(k_ms: int) -> list[dict]:
    return [
        {
            "sensor_id": "goes18_fdcc",
            "scenario_id": "fixture",
            "kind": "goes_pixel",
            "name": "GOES-18 FDCC",
            "platform": "GOES-18",
            "provenance": "simulated",
            "enabled": True,
            "status": "ok",
            "last_data_sim_ms": k_ms,
            "latency_s": 0.0,
            "lon": None,
            "lat": None,
            "heading_deg": None,
            "hfov_deg": None,
            "pixel_m": 2000.0,
        },
        {
            "sensor_id": "viirs_n20",
            "scenario_id": "fixture",
            "kind": "viirs_pixel",
            "name": "VIIRS NOAA-20",
            "platform": "NOAA-20",
            "provenance": "simulated",
            "enabled": True,
            "status": "ok",
            "last_data_sim_ms": None,
            "latency_s": None,
            "lon": None,
            "lat": None,
            "heading_deg": None,
            "hfov_deg": None,
            "pixel_m": 375.0,
        },
    ]


def activity(k_ms: int, cycle_id: str, n_hot: int, alerts: list[dict]) -> list[dict]:
    proposals = [a for a in alerts if a["state"] == "proposed"]
    messages = [
        ("observation", "info", f"FIXTURE GOES-18 {hm(k_ms)} → {n_hot} hot px, 90 clear", None, None, None),
        ("filter", "info", "FIXTURE Filter: ESS 16/16 (α 1.00), not resampled", None, None, None),
        ("forecast", "info", "FIXTURE Forecast run 0.1 s → 12 leads", None, None, 100),
    ]
    for alert in proposals:
        percent = alert["text"].split("(P ", 1)[1].split(")", 1)[0]
        lead = "2h" if alert["rule_id"].startswith("R1") else "1h"
        messages.append(
            (
                "alert",
                "notice",
                f"FIXTURE {alert['zone_ids'][0]} crossed {alert['rule_id'].split('_')[0]} (P {percent} by +{lead})",
                "alert",
                alert["alert_id"],
                None,
            )
        )
    events = []
    for i, (stage, level, message, ref_kind, ref_id, duration) in enumerate(messages[:5]):
        if len(message) > 140 or not message.startswith("FIXTURE"):
            raise RuntimeError(message)
        events.append(
            {
                "event_id": f"ev_{k_ms}_{i}",
                "scenario_id": "fixture",
                "branch_id": "main",
                "sim_ms": k_ms,
                "wall_ms": CREATED_WALL_MS,
                "stage": stage,
                "actor": "fixture",
                "level": level,
                "message": message,
                "ref_kind": ref_kind,
                "ref_id": ref_id,
                "duration_ms": duration,
            }
        )
    if not 3 <= len(events) <= 5:
        raise RuntimeError(f"{len(events)} events")
    return events


def params_summary() -> dict:
    return {
        "ros_mult": [1.0, round(float(np.std([m[0] / ROS_M_PER_MIN for m in MEMBERS])), 6)],
        "heading_deg": [HEADING, round(float(np.std([m[1] - HEADING for m in MEMBERS])), 6)],
    }


def build_cycles(zones: list[Zone], t_q_abs: np.ndarray) -> None:
    if FIXTURE_MAIN.exists():
        shutil.rmtree(FIXTURE_MAIN)
    history: list[dict] = []
    prev_p120: dict[str, float] = {}
    prev_ha: float | None = None
    index_cycles = []
    summary: list[str] = []

    for step, minutes_before in enumerate((15, 10, 5)):
        k_ms = IGN_MS - minutes_before * 60_000
        scan, _ = scan_and_obs(k_ms, f"pre_{step}", 1)
        scan["n_hot"] = 0
        scan["n_pixels_aoi"] = scan["n_clear"]
        bundle = {
            "schema_version": SCHEMA_VERSION,
            "bundle_id": f"bnd_fixture_main_{k_ms}",
            "scenario_id": "fixture",
            "branch_id": "main",
            "knowledge_sim_ms": k_ms,
            "cycle": None,
            "zone_risk": [],
            "observations": [],
            "scan_events": [scan],
            "sensors": [],
            "alerts": [],
            "briefings": [],
            "people": [],
            "reports": [],
            "metrics": [],
            "events": [],
        }
        _write_bundle(k_ms, bundle, index_cycles)

    for n in range(1, N_CYCLES + 1):
        k_ms = IGN_MS + n * CYCLE_MIN * 60_000
        cycle_id = f"cyc_fixture_main_{k_ms}"
        issue_age = n * CYCLE_MIN
        t_after = t_q_abs - issue_age
        encoded = encode_arrival(t_after)
        burned = t_q_abs[2] <= issue_age
        front_km, burned_geom, front_geom = vectorize_mask(burned)
        _, danger60, _ = vectorize_mask(t_q_abs[1] <= issue_age + 60)
        _, danger120, _ = vectorize_mask(t_q_abs[1] <= issue_age + 120)
        danger = {"lead60_p30": danger60, "lead120_p30": danger120}
        if len(json.dumps(danger)) > 20_000:
            raise RuntimeError(f"danger geometry exceeds 20 KB at cycle {n}")
        cell_ha = (CELL * CELL) / 10_000
        burned_ha = round(float(burned.sum()) * cell_ha, 4)
        growth = 0.0 if prev_ha is None else round((burned_ha - prev_ha) * (60 / CYCLE_MIN), 4)
        prev_ha = burned_ha
        rel = f"cache/fixture/main/{k_ms}"
        open_orders = {
            a["zone_ids"][0]
            for a in history
            if a["level"] == "order_recommended" and a["state"] in {"proposed", "approved", "calling", "called", "acknowledged"}
        }
        rows = [zone_risk_row(zone, cycle_id, k_ms, prev_p120, open_orders) for zone in zones]
        alerts = propose(zones, k_ms, cycle_id, history)
        open_orders = {
            a["zone_ids"][0]
            for a in history
            if a["level"] == "order_recommended" and a["state"] in {"proposed", "approved", "calling", "called", "acknowledged"}
        }
        for row in rows:
            if row["zone_id"] in open_orders:
                row["status"] = "order"
        rank_zones(rows)
        scan, obs = scan_and_obs(k_ms, cycle_id, n)
        for record in obs:
            if record["available_sim_ms"] > k_ms:
                raise RuntimeError("observation is from the future")
        events = activity(k_ms, cycle_id, scan["n_hot"], alerts)
        for alert in alerts:
            if alert["state"] == "proposed":
                summary.append(f"cycle {n} {alert['zone_ids'][0]} {alert['rule_id']}")
        bundle = {
            "schema_version": SCHEMA_VERSION,
            "bundle_id": f"bnd_fixture_main_{k_ms}",
            "scenario_id": "fixture",
            "branch_id": "main",
            "knowledge_sim_ms": k_ms,
            "cycle": {
                "cycle_id": cycle_id,
                "scenario_id": "fixture",
                "branch_id": "main",
                "knowledge_sim_ms": k_ms,
                "data_through_sim_ms": k_ms,
                "created_wall_ms": CREATED_WALL_MS,
                "source": "cache",
                "filter_mode": "none",
                "n_members": N_MEMBERS,
                "ess": float(N_MEMBERS),
                "tempering_alpha": 1.0,
                "resampled": False,
                "n_obs_by_kind": {"goes_pixel": scan["n_hot"]},
                "horizon_min": HORIZON_MIN,
                "step_min": 30,
                "assets": {
                    "arrival_q": f"{rel}/arrival_q.bin",
                    "perimeter": f"{rel}/perimeter.geojson",
                    "isochrones": f"{rel}/isochrones.geojson",
                },
                "danger": danger,
                "burned_ha": burned_ha,
                "front_km": round(front_km, 4),
                "growth_ha_per_h": growth,
                "head_bearing_deg": HEADING,
                "params_summary": params_summary(),
                "model_version": "fixture-ellipse-1",
                "config_hash": None,
                "duration_ms": 1,
            },
            "zone_risk": rows,
            "observations": obs,
            "scan_events": [scan],
            "sensors": sensors(k_ms),
            "alerts": alerts,
            "briefings": [],
            "people": [],
            "reports": [],
            "metrics": [],
            "events": events,
        }
        validated = CycleBundle.model_validate(bundle)
        out = FIXTURE_MAIN / str(k_ms)
        out.mkdir(parents=True, exist_ok=True)
        encoded.tofile(out / "arrival_q.bin")
        write_json(out / "perimeter.geojson", _feature_collection(burned_geom, front_geom))
        write_json(out / "isochrones.geojson", _isochrones(t_q_abs[2], issue_age))
        write_json(out / "bundle.json", validated.model_dump(mode="json"))
        index_cycles.append({"knowledge_sim_ms": k_ms, "bundle": f"{rel}/bundle.json"})

    write_json(
        FIXTURE_MAIN / "index.json",
        {"scenario_id": "fixture", "branch_id": "main", "cycles": index_cycles},
    )
    print("fixture alerts:")
    for line in summary:
        print(f"  {line}")


def _write_bundle(k_ms: int, bundle: dict, index_cycles: list[dict]) -> None:
    validated = CycleBundle.model_validate(bundle)
    out = FIXTURE_MAIN / str(k_ms)
    write_json(out / "bundle.json", validated.model_dump(mode="json"))
    index_cycles.append({"knowledge_sim_ms": k_ms, "bundle": f"cache/fixture/main/{k_ms}/bundle.json"})


def _feature_collection(burned: dict, front: dict) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "id": "burned", "properties": {"kind": "burned"}, "geometry": burned},
            {"type": "Feature", "id": "front", "properties": {"kind": "front"}, "geometry": front},
        ],
    }


def _isochrones(t50_abs: np.ndarray, issue_age: float) -> dict:
    features = []
    for lead in range(60, 361, 60):
        _, _, front = vectorize_mask(t50_abs <= issue_age + lead)
        if not front["coordinates"]:
            continue
        geometry = (
            {"type": "LineString", "coordinates": front["coordinates"][0]}
            if len(front["coordinates"]) == 1
            else front
        )
        features.append(
            {"type": "Feature", "properties": {"lead_min": lead, "q": 0.5}, "geometry": geometry}
        )
    return {"type": "FeatureCollection", "features": features}


def write_static(zones: list[Zone], corners: dict[str, list[float]]) -> None:
    if FIXTURE_STATIC.exists():
        shutil.rmtree(FIXTURE_STATIC)
    FIXTURE_STATIC.mkdir(parents=True)
    lons = [pt[0] for pt in corners.values()]
    lats = [pt[1] for pt in corners.values()]
    west, east = min(lons), max(lons)
    south, north = min(lats), max(lats)
    last_ms = IGN_MS + N_CYCLES * CYCLE_MIN * 60_000
    manifest = {
        "scenario_id": "fixture",
        "title": "FIXTURE (synthetic)",
        "tz": "America/Los_Angeles",
        "config_hash": None,
        "is_fixture": True,
        "honesty": {
            "real": [],
            "simulated": ["ellipse fire", "zones", "sensors", "residents", "terrain"],
        },
        "aoi_bbox": [round(west, 6), round(south, 6), round(east, 6), round(north, 6)],
        "initial_view": {
            "longitude": round((west + east) / 2, 6),
            "latitude": round((south + north) / 2, 6),
            "zoom": 11,
            "pitch": 45,
            "bearing": 0,
        },
        "t_ignition_sim_ms": IGN_MS,
        "replay_start_sim_ms": IGN_MS - 15 * 60_000,
        "replay_end_sim_ms": last_ms,
        "horizon_min": HORIZON_MIN,
        "step_min": 30,
        "quantiles": [0.1, 0.3, 0.5, 0.7, 0.9],
        "grid": {
            "nx": NX,
            "ny": NY,
            "cell_m": CELL,
            "crs": "EPSG:32611",
            "corners": corners,
        },
        "terrain": {
            "elevation_url": "static/fixture/terrain_elev.png",
            "encoding": "terrarium",
            "bounds": [round(west, 6), round(south, 6), round(east, 6), round(north, 6)],
            "texture_url": "static/fixture/terrain_tex.jpg",
            "texture_date": "fixture",
            "attribution": ["Synthetic flat terrain for the Emberline fixture"],
        },
        "static": {
            "zones_url": "static/fixture/zones.geojson",
            "assets_url": "static/fixture/assets.geojson",
            "sensors_url": "static/fixture/sensors.json",
            "satellites_url": "static/fixture/satellites.json",
            "timeline_url": "static/fixture/timeline.json",
            "area_x_url": "static/fixture/area_x.geojson",
            "spawn_points_url": "static/fixture/spawn_points.json",
            "scans_dir": None,
        },
        "lake_ave": [[-118.1316, 34.19], [-118.1316, 34.235]],
    }
    write_json(FIXTURE_STATIC / "manifest.json", manifest)
    features = []
    spawns = []
    for zone in zones:
        features.append(
            {
                "type": "Feature",
                "id": zone.zone_id,
                "properties": {
                    "zone_id": zone.zone_id,
                    "name": zone.name,
                    "population": zone.population,
                    "housing": zone.housing,
                    "n_critical": zone.n_critical,
                    "tier": zone.tier,
                    "evac_min_default": EVAC_MIN,
                    "egress_roads": zone.egress_roads,
                    "approximate": True,
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [ring_lonlat(rectangle_utm(zone.center_along, zone.center_cross))],
                },
            }
        )
        lon, lat = center_lonlat(zone.center_along, zone.center_cross)
        spawns.append(
            {
                "spawn_id": f"fix-spawn-{zone.zone_id}",
                "lon": round(lon, 6),
                "lat": round(lat, 6),
                "zone_id": zone.zone_id,
                "street": f"Fixture Lane {zone.zone_id[-1]}",
            }
        )
    write_json(FIXTURE_STATIC / "zones.geojson", {"type": "FeatureCollection", "features": features})
    write_json(FIXTURE_STATIC / "spawn_points.json", {"demo": spawns})

    asset_specs = (
        ("fix-a1", "school", "Fixture school", "FIX-Z3"),
        ("fix-a2", "fire_station", "Fixture fire station", "FIX-Z2"),
        ("fix-a3", "water_plant", "Fixture water plant", "FIX-Z1"),
    )
    by_id = {zone.zone_id: zone for zone in zones}
    asset_features = []
    for asset_id, kind, name, zone_id in asset_specs:
        zone = by_id[zone_id]
        lon, lat = center_lonlat(zone.center_along, zone.center_cross)
        asset_features.append(
            {
                "type": "Feature",
                "properties": {"asset_id": asset_id, "type": kind, "name": name, "zone_id": zone_id},
                "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
            }
        )
    write_json(FIXTURE_STATIC / "assets.geojson", {"type": "FeatureCollection", "features": asset_features})
    write_json(FIXTURE_STATIC / "sensors.json", sensors(IGN_MS))
    # TLE lines are a placeholder shape, not a real NOAA-20 element set.
    write_json(
        FIXTURE_STATIC / "satellites.json",
        [
            {
                "sat_id": "goes18",
                "name": "GOES-18",
                "orbit": "geo",
                "geo_lon_deg": -137.2,
                "sensor": "goes18_fdcc",
                "pixel_m": 2000,
            },
            {
                "sat_id": "noaa20",
                "name": "NOAA-20",
                "orbit": "leo",
                "norad_id": 43013,
                "tle": [
                    "1 00000U 00000A   25008.00000000  .00000000  00000-0  00000-0 0  0000",
                    "2 00000  00.0000 000.0000 0000000 000.0000 000.0000 00.00000000000000",
                ],
                "sensor": "viirs_n20",
                "pixel_m": 375,
            },
        ],
    )
    write_json(
        FIXTURE_STATIC / "timeline.json",
        [
            {"t_sim_ms": IGN_MS, "label": "FIXTURE ignition", "source": "fixture", "kind": "official"},
            {
                "t_sim_ms": IGN_MS + 20 * CYCLE_MIN * 60_000,
                "label": "FIXTURE zone warning",
                "source": "fixture",
                "kind": "satellite",
            },
        ],
    )
    area = orient(
        Polygon([(-118.165, 34.190), (-118.1316, 34.190), (-118.1316, 34.235), (-118.165, 34.235)]),
        sign=1.0,
    )
    write_json(
        FIXTURE_STATIC / "area_x.geojson",
        {
            "type": "Feature",
            "properties": {"name": "west Altadena foothills (area X)"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[round(lon, 6), round(lat, 6)] for lon, lat in area.exterior.coords]],
            },
        },
    )
    Image.new("RGB", (256, 256), (128, 0, 0)).save(FIXTURE_STATIC / "terrain_elev.png", optimize=True)
    Image.new("RGB", (512, 512), (128, 128, 128)).save(FIXTURE_STATIC / "terrain_tex.jpg", quality=85, optimize=True)


def grid_corners() -> dict[str, list[float]]:
    pairs = {
        "tl": (X0, Y0),
        "tr": (X0 + WIDTH, Y0),
        "br": (X0 + WIDTH, Y_SOUTH),
        "bl": (X0, Y_SOUTH),
    }
    return {name: [round(utm_inverse(e, n)[0], 6), round(utm_inverse(e, n)[1], 6)] for name, (e, n) in pairs.items()}


def check_self() -> None:
    if len(MEMBERS) != N_MEMBERS:
        raise RuntimeError(f"expected {N_MEMBERS} members")
    back_e, back_n = utm_forward(*utm_inverse(*utm_forward(IGN_LON, IGN_LAT)))
    if abs(back_e - utm_forward(IGN_LON, IGN_LAT)[0]) > 1 or abs(back_n - utm_forward(IGN_LON, IGN_LAT)[1]) > 1:
        raise RuntimeError("UTM round-trip is off by more than a metre")
    ign_e, ign_n = utm_forward(IGN_LON, IGN_LAT)
    if not (X0 < ign_e < X0 + WIDTH and Y_SOUTH < ign_n < Y0):
        raise RuntimeError(f"ignition {ign_e}, {ign_n} is outside the Eaton grid")
    de, dn = along_cross_offset(ROS_M_PER_MIN * 60, 0)
    got = float(arrival_minutes(de, dn, HEADING, ROS_M_PER_MIN))
    if abs(got - 60) > 0.05:
        raise RuntimeError(f"head arrival {got} min, expected 60")


def fixture_bytes() -> int:
    root = REPO / "fixtures"
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def main() -> None:
    check_self()
    zones = place_zones()
    t_q = grid_arrivals()
    write_static(zones, grid_corners())
    build_cycles(zones, t_q)
    size = fixture_bytes()
    print(f"fixture bytes: {size}")
    if size >= 10 * 1024 * 1024:
        raise RuntimeError(f"fixture is {size} bytes")


if __name__ == "__main__":
    main()
