"""Web manifest and static files (01 §10): web/{manifest,sensors,satellites}.json.

`python -m ember.data.webpack --scenario eaton` also builds story geometry, terrain and the
timeline if their outputs are missing. Zones, assets and spawn points come from T9a (data/vectors.py,
data/exposure.py); this module only references them.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx

from ember.contracts import SensorInfo
from ember.data import story, terrain, truth
from ember.data.config import load_scenario, scenario_dir, utc_ms

CELESTRAK = "https://celestrak.org/NORAD/elements/gp.php?CATNR={norad}&FORMAT=TLE"
LEO_NOTE = "orbit illustrative; scan time real"

# sensor_id -> (kind, name, platform, nominal pixel_m)
# ponytail: nominal sizes; GOES-18 at Altadena is ~2.3 x 3 km (research/06 §2), 2 km is the sub-point figure
SENSORS: dict[str, tuple[str, str, str, float]] = {
    "goes18_fdcc": ("goes_pixel", "GOES-18 ABI FDC (CONUS)", "GOES-18", 2000.0),
    "goes16_fdcc": ("goes_pixel", "GOES-16 ABI FDC (CONUS)", "GOES-16", 2000.0),
    "viirs_n20": ("viirs_pixel", "VIIRS 375 m (NOAA-20)", "NOAA-20", 375.0),
    "viirs_snpp": ("viirs_pixel", "VIIRS 375 m (S-NPP)", "S-NPP", 375.0),
    "viirs_n21": ("viirs_pixel", "VIIRS 375 m (NOAA-21)", "NOAA-21", 375.0),
}

# (sat_id, display name, sub-point lon, sensor, note). pixel_m is labelled approximate in the data (WAVE2 §0.7).
GEO = [
    ("goes18", "GOES-18", -137.2, "goes18_fdcc",
     "pixel_m is the nominal sub-point size; ~2.3 x 3 km at Altadena (research/06 §2, own estimate)"),
    ("goes16", "GOES-16", -75.2, "goes16_fdcc",
     "pixel_m is the nominal sub-point size; larger at Altadena (oblique view). "
     "goes16_fdcc is not assimilated here, so sensors.json has no row for it"),
]  # fmt: skip
# (sat_id, display name, NORAD id, CelesTrak name prefix that must match, sensor)
LEO = [
    ("snpp", "Suomi NPP", 37849, "SUOMI NPP", "viirs_snpp"),
    ("noaa20", "NOAA-20", 43013, "NOAA 20", "viirs_n20"),
    ("noaa21", "NOAA-21", 54234, "NOAA 21", "viirs_n21"),
]


# ---------- sensors ----------


# Honesty: a source is claimed as real (manifest) or live (sensors) only when its data is in the package.
EVIDENCE = {
    "GOES-18 FDC": "obs/goes18_fdcc.parquet",
    "VIIRS 375m": "obs/viirs.parquet",
    "HRRR winds": "forcing/hrrr_*.nc",
    "USGS 3DEP": "web/terrain_elev.png",
    "LANDFIRE LF2024": "static/fbfm40.tif",
    "Census 2020": "vectors/zones.geojson",
    "LA County zone names": "vectors/zones.geojson",
    "official timeline": "truth/timeline.json",
}
SENSOR_DATA = {"goes18_fdcc": "obs/goes18_fdcc.parquet", "viirs_n20": "obs/viirs.parquet",
               "viirs_snpp": "obs/viirs.parquet", "viirs_n21": "obs/viirs.parquet"}


def has_data(scn: str, rel: str) -> bool:
    return any(scenario_dir(scn).glob(rel))


# The zone source is an event snapshot, so the zone set itself reflects what happened that night.
# The manifest's honesty badge says so (00 §4.1 has no notes field, so it rides on the label).
ZONES_LABEL = "LA County zone names"
ZONES_DISCLOSED = ("LA County zones (Jan 2025 Eaton/Palisades evacuation snapshot: only zones warned or "
                   "ordered that night are included; status/time fields dropped; names derived from zone codes)")


def zones_from_snapshot(scn: str) -> bool:
    path = scenario_dir(scn) / "vectors" / "zones.geojson"
    if not path.exists():
        return False
    meta = json.loads(path.read_text(encoding="utf-8")).get("metadata", {})
    return "snapshot" in meta.get("source", "") + meta.get("note", "")


def honesty(scn: str, cfg: dict) -> dict:
    """scenario.yaml's honesty block, minus the real sources whose data isn't built yet (unknown labels kept),
    with the zone label replaced by a disclosure when the zones come from the event snapshot."""
    real = [s for s in cfg["honesty"]["real"] if s not in EVIDENCE or has_data(scn, EVIDENCE[s])]
    dropped = [s for s in cfg["honesty"]["real"] if s not in real]
    if dropped:
        print(f"honesty: not claiming {dropped} (no data in the package yet)")
    if zones_from_snapshot(scn):
        real = [ZONES_DISCLOSED if s == ZONES_LABEL else s for s in real]
    return {"real": real, "simulated": list(cfg["honesty"]["simulated"])}


def sensors(scn: str, cfg: dict) -> list[dict]:
    rows = []
    for sid in cfg["assimilate_sensors"]:
        kind, name, platform, pixel_m = SENSORS[sid]
        live = sid not in SENSOR_DATA or has_data(scn, SENSOR_DATA[sid])
        info = SensorInfo(
            sensor_id=sid, scenario_id=scn, kind=kind, name=name, platform=platform,
            provenance="real", pixel_m=pixel_m, enabled=live, status="ok" if live else "down",
        )  # fmt: skip
        rows.append(info.model_dump(mode="json"))
    return rows


# ---------- satellites ----------


def tle_checksum_ok(line: str) -> bool:
    s = sum(int(c) if c.isdigit() else (1 if c == "-" else 0) for c in line[:68])
    return s % 10 == int(line[68])


def parse_tle(text: str, norad: int, name_prefix: str) -> tuple[str, list[str]]:
    """Verify CelesTrak's 3-line answer: name, NORAD id on both lines, checksums."""
    lines = [ln.rstrip() for ln in text.strip().splitlines() if ln.strip()]
    assert len(lines) == 3, f"NORAD {norad}: expected 3 TLE lines, got {lines!r}"
    name, l1, l2 = lines
    assert name.strip().upper().startswith(name_prefix), f"NORAD {norad} is {name.strip()!r}, expected {name_prefix}"
    assert l1.startswith("1 ") and l2.startswith("2 "), f"NORAD {norad}: bad TLE line numbers"
    assert int(l1[2:7]) == norad and int(l2[2:7]) == norad, f"NORAD id mismatch in TLE for {norad}"
    assert tle_checksum_ok(l1) and tle_checksum_ok(l2), f"NORAD {norad}: TLE checksum failed"
    return name.strip(), [l1, l2]


def fetch_tle(scn: str, norad: int, force: bool = False) -> str:
    path = scenario_dir(scn) / "raw" / "celestrak" / f"{norad}.tle"
    if path.exists() and not force:
        return path.read_text(encoding="utf-8")
    text = httpx.get(CELESTRAK.format(norad=norad), timeout=60).raise_for_status().text
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


def satellites(scn: str, force: bool = False) -> list[dict]:
    rows: list[dict] = [
        {
            "sat_id": sid, "name": name, "orbit": "geo", "geo_lon_deg": lon, "sensor": sensor,
            "pixel_m": SENSORS[sensor][3], "approximate": True, "note": note,
        }  # fmt: skip
        for sid, name, lon, sensor, note in GEO
    ]
    for sid, name, norad, prefix, sensor in LEO:
        _, tle = parse_tle(fetch_tle(scn, norad, force), norad, prefix)
        rows.append(
            {
                "sat_id": sid, "name": name, "orbit": "leo", "norad_id": norad, "tle": tle,
                "sensor": sensor, "pixel_m": SENSORS[sensor][3], "note": LEO_NOTE,
            }
        )  # fmt: skip
    return rows


# ---------- manifest ----------


def initial_view(cfg: dict) -> dict:
    # ponytail: framed on area X / Lake Ave, where the story happens; Linda tunes zoom/pitch in the UI
    a = cfg["area_x"]
    return {
        "longitude": round((a["lon_min"] + cfg["ignition"]["lon"]) / 2, 6),
        "latitude": round((a["lat_min"] + a["lat_max"]) / 2, 6),
        "zoom": 12,
        "pitch": 50,
        "bearing": 0,
    }


def manifest(scn: str, cfg: dict, terrain_blk: dict, lake_ave: list[list[float]]) -> dict:
    grid = json.loads((scenario_dir(scn) / "grid.json").read_text(encoding="utf-8"))
    g = cfg["grid"]
    assert (grid["nx"], grid["ny"], grid["cell_m"], grid["crs"]) == (g["nx"], g["ny"], g["cell_m"], g["crs"])
    s = f"static/{scn}"
    return {
        "scenario_id": scn,
        "title": cfg["title"].replace(" replay", ""),  # cfg feeds run_hash, so the yaml title stays
        "tz": cfg["tz"],
        "config_hash": None,
        "is_fixture": False,
        "honesty": honesty(scn, cfg),
        "aoi_bbox": list(terrain.aoi_bbox(scn)),
        "initial_view": initial_view(cfg),
        "t_ignition_sim_ms": utc_ms(cfg["t_ignition_utc"]),
        "replay_start_sim_ms": utc_ms(cfg["replay_start_utc"]),
        "replay_end_sim_ms": utc_ms(cfg["replay_end_utc"]),
        "horizon_min": cfg["horizon_min"],
        "step_min": cfg["step_min"],
        "quantiles": list(cfg["quantiles"]),
        "grid": {
            "nx": g["nx"],
            "ny": g["ny"],
            "cell_m": g["cell_m"],
            "crs": "EPSG:32611",
            "corners": {k: [round(v, 6) for v in grid["corners"][k]] for k in ("tl", "tr", "br", "bl")},
        },
        "terrain": terrain_blk,
        "static": {
            "zones_url": f"{s}/zones.geojson",
            "assets_url": f"{s}/assets.geojson",
            "sensors_url": f"{s}/sensors.json",
            "satellites_url": f"{s}/satellites.json",
            "timeline_url": f"{s}/timeline.json",
            "area_x_url": f"{s}/area_x.geojson",
            "spawn_points_url": f"{s}/spawn_points.json",
            "scans_dir": None,
        },
        "lake_ave": lake_ave,
    }


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def build(scn: str, force: bool = False) -> dict:
    cfg = load_scenario(scn)
    web = scenario_dir(scn) / "web"
    lake = story.build(scn, force)
    terrain_blk = terrain.build(scn, force)
    if force or not (web / "timeline.json").exists():
        truth.build(scn)
    _write_json(web / "sensors.json", sensors(scn, cfg))
    _write_json(web / "satellites.json", satellites(scn, force))
    m = manifest(scn, cfg, terrain_blk, lake)
    _write_json(web / "manifest.json", m)
    missing = [k for k, v in m["static"].items() if v and not (web / Path(v).name).exists()]
    if missing:
        print(f"note: not written yet (T9a owns them): {missing}")
    return m


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="eaton")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    print(json.dumps(build(a.scenario, a.force)["terrain"], indent=2))
