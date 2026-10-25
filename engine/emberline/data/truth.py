"""Documented timeline (01 §9): web/timeline.json and truth/timeline.json (same content).

Times are the sources' PST wall clock (UTC-8 in January), stored as UTC epoch ms.
`as_observation` rows are display-only unless scenario.yaml sets use_documented_reports.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

from emberline.data.config import scenario_dir
from emberline.data.story import street_intersection

PST = timezone(timedelta(hours=-8))
WEST_ALTADENA = (-118.175, 34.17, -118.12, 34.23)  # search box for intersections (w, s, e, n)

# (PST "YYYY-MM-DD HH:MM", label, kind, source, extra). Labels/sources per 01 §9 and research/06 §2.
EVENTS: list[tuple[str, str, str, str, dict]] = [
    ("2025-01-07 18:15", "Ignition, Eaton Canyon", "official", "Citygate (May 2026)", {}),
    ("2025-01-07 18:45", "Aircraft grounded by wind", "official", "Citygate; LA County AAR", {}),
    ("2025-01-07 18:48", "Advisory alerts to east Altadena zones", "official", "Citygate; LA County AAR", {}),
    ("2025-01-07 19:26", "First evacuation orders (east Altadena)", "official", "Citygate; LA County AAR", {}),
    ("2025-01-07 22:50", "911 call: fire visible from E. Calaveras St", "report", "LA Times", {}),
    (
        "2025-01-07 23:49",
        "First structure fire west of Lake (Glenrose Ave and W. Loma Alta Dr)",
        "report",
        "Citygate (Battalion Chief report)",
        {"as_observation": True, "streets": ("Glenrose Avenue", "West Loma Alta Drive")},
    ),
    (
        "2025-01-08 01:30",
        "First VIIRS detection",
        "satellite",
        "NASA SVS",
        {"approximate": True, "note": "Source gives ~01:30 without a time zone; read as PST (01:30Z precedes ignition)."},
    ),
    (
        "2025-01-08 02:18",
        "Radio: fire moving west in foothills north of Farnsworth Park",
        "report",
        "Citygate; LA County AAR",
        # Manual geocode, no intersection to look up: lon = centroid of OSM way 164247879 (Charles S.
        # Farnsworth Park, Geofabrik socal-261023.osm.pbf), lat = the park's north edge (34.2021) + 400 m,
        # so the 400 m report disk sits in the foothills just north of the park.
        {"as_observation": True, "lonlat": (-118.1328, 34.2057),
         "note": "Hand-placed 400 m north of Farnsworth Park (OSM); the report names no street."},
    ),
    ("2025-01-08 03:25", "Evacuation orders for 13 west-Altadena zones", "official", "Citygate; LA County AAR; FSRI", {}),
    ("2025-01-08 05:13", "FireGuard: front ~1 block west of Lake Ave", "report", "Citygate", {}),
]


def pst_to_utc_ms(s: str) -> int:
    dt = datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=PST)
    return int(dt.timestamp() * 1000)


def event_row(scn: str, pst: str, label: str, kind: str, source: str, extra: dict) -> dict:
    row: dict = {"t_sim_ms": pst_to_utc_ms(pst), "label": label, "source": source, "kind": kind}
    extra = dict(extra)
    streets = extra.pop("streets", None)
    lonlat = extra.pop("lonlat", None)
    row.update(extra)
    if not row.get("as_observation"):
        return row
    hit = street_intersection(scn, *streets, WEST_ALTADENA) if streets else lonlat
    row["approximate"] = True
    if hit is None:
        row["note"] = "No street intersection in the source; location left out." if not streets else (
            f"OSM intersection of {streets[0]} and {streets[1]} not found; location left out."
        )
        return row
    row["lon"], row["lat"] = hit
    row["note"] = row.get("note") or f"Geocoded to the OSM intersection of {streets[0]} and {streets[1]}; the report names the intersection only."
    return row


def build(scn: str) -> list[dict]:
    rows = [event_row(scn, *ev) for ev in EVENTS]
    assert [r["t_sim_ms"] for r in rows] == sorted(r["t_sim_ms"] for r in rows)
    text = json.dumps(rows, indent=2) + "\n"
    for sub in ("web", "truth"):
        p = scenario_dir(scn) / sub / "timeline.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="eaton")
    a = ap.parse_args()
    for r in build(a.scenario):
        print(r)
