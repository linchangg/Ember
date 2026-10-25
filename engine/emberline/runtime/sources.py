"""Engine data sources, released at their real availability times. spec/03_engine.md §3.

Core rule: no future data. Everything `collect(a, b)` returns has `available_sim_ms <= b`,
asserted in code.

Input contract (wave-2 T8/T13 must write exactly these columns; paths relative to scn_dir):

- `replay_manifest.parquet` (spec/01 §8): entry_id, source, kind ("observation"|"forcing"),
  scan_start_ms, scan_end_ms, available_ms, latency_basis, local_path, rows, n_hot, provenance.
  For observations, `local_path` points at the pixel table (relative to scn_dir) and
  `entry_id` is the scan_id (GOES) or the pass_id (VIIRS). VIIRS rows have a `source`
  starting with "viirs".
- `obs/goes18_fdcc.parquet` (spec/01 §3): scan_id, sensor_id, scan_start_ms, scan_end_ms,
  created_ms, s3_last_modified_ms, available_ms, px_x, px_y, lon, lat,
  ring (list of [lon, lat]), mask, dqf, hot, clear, frp_mw, trust.
- `obs/scan_events.parquet`: the ScanEvent field names minus scenario_id/branch_id
  (scan_id, sensor_id, mode, t_start_sim_ms, t_end_sim_ms, available_sim_ms, n_pixels_aoi,
  n_hot, n_clear, assimilated, pixels_path, provenance). `assimilated` is overridden here.
- `obs/viirs.parquet` (spec/01 §4): pass_id, sensor_id, row, t_obs_ms, available_ms, lon,
  lat, ring, trust, confidence, frp_mw.

ForcingProvider (step 6 of the T7 brief) remains to be assembled. The HazardModel
API and hazard.integration.weather_from_hrrr availability adapter are now available.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pandas as pd

from emberline.contracts import Footprint, ObservationRecord, ScanEvent

GOES_STALE_MS = 15 * 60_000
SYN_SENSOR = "synthetic_goes"
SYN_LATENCY_MS = 3 * 60_000
SYN_HALF_M = 1000.0  # ~2 km square footprint


def _none(v: Any) -> Any:
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else v


def _ring(ring: Any) -> list[tuple[float, float]]:
    return [(float(p[0]), float(p[1])) for p in ring]


def _assert_due(items: list[tuple[str, int]], b_ms: int) -> None:
    for item_id, avail in items:
        if avail > b_ms:
            raise AssertionError(f"future data: {item_id} available {avail} > {b_ms}")


def hot_only(obs: list[ObservationRecord]) -> list[ObservationRecord]:
    """Clear pixels go to physics but are never published."""
    return [o for o in obs if o.hot]


def _check_window(obs: list[ObservationRecord], scans: list[ScanEvent], b_ms: int) -> None:
    _assert_due([(o.obs_id, o.available_sim_ms) for o in obs], b_ms)
    _assert_due([(s.scan_id, s.available_sim_ms) for s in scans], b_ms)


class ReplaySource:
    def __init__(self, scn_dir: Path, scenario_id: str, assimilate_sensors: list[str]):
        self.scenario_id = scenario_id
        self.assimilate = set(assimilate_sensors)
        man = pd.read_parquet(scn_dir / "replay_manifest.parquet")
        man = man[man["kind"] == "observation"]
        self.manifest = man.sort_values(["available_ms", "entry_id"], kind="stable")
        self.tables = {p: pd.read_parquet(scn_dir / p) for p in self.manifest["local_path"].unique()}
        ev_path = scn_dir / "obs" / "scan_events.parquet"
        events = pd.read_parquet(ev_path) if ev_path.exists() else pd.DataFrame(columns=["scan_id"])
        self.events = {r["scan_id"]: r for r in events.to_dict("records")}

    def collect(self, a_ms: int, b_ms: int) -> tuple[list[ObservationRecord], list[ScanEvent]]:
        win = self.manifest[(self.manifest["available_ms"] > a_ms) & (self.manifest["available_ms"] <= b_ms)]
        obs: list[ObservationRecord] = []
        scans: list[ScanEvent] = []
        for m in win.to_dict("records"):
            table = self.tables[m["local_path"]]
            if str(m["source"]).startswith("viirs"):
                rows = table[table["pass_id"] == m["entry_id"]]
                o, s = self._viirs(m, rows)
            else:
                rows = table[table["scan_id"] == m["entry_id"]]
                o, s = self._goes(m["entry_id"], rows)
            if s.assimilated:
                obs.extend(o)
            scans.append(s)
        _check_window(obs, scans, b_ms)
        return obs, scans

    def _goes(self, scan_id: str, rows: pd.DataFrame) -> tuple[list[ObservationRecord], ScanEvent]:
        ev = {k: _none(v) for k, v in self.events[scan_id].items()}
        ev["assimilated"] = ev["sensor_id"] in self.assimilate
        scan = ScanEvent(**ev, scenario_id=self.scenario_id)
        obs = [
            ObservationRecord(
                obs_id=f"obs_{r['sensor_id']}_{r['scan_id']}_{r['px_x']}_{r['px_y']}",
                scenario_id=self.scenario_id,
                sensor_id=r["sensor_id"],
                kind="goes_pixel",
                observed_sim_ms=int(r["scan_start_ms"]),
                available_sim_ms=int(r["available_ms"]),
                geometry=Footprint(ring=_ring(r["ring"])),
                hot=bool(r["hot"]),
                footprint_key=f"goes18:{r['px_x']}:{r['px_y']}",
                fdc_class=int(r["mask"]),
                frp_mw=_none(r["frp_mw"]),
                trust=float(r["trust"]),
                provenance="real",
                source_ref=r["scan_id"],
            )
            for r in rows.to_dict("records")
            if r["hot"] or r["clear"]
        ]
        return obs, scan

    def _viirs(self, m: dict, rows: pd.DataFrame) -> tuple[list[ObservationRecord], ScanEvent]:
        recs = rows.to_dict("records")
        sensor_id = recs[0]["sensor_id"] if recs else m["source"]
        obs = [
            ObservationRecord(
                obs_id=f"obs_{r['sensor_id']}_{r['t_obs_ms']}_{r['row']}",
                scenario_id=self.scenario_id,
                sensor_id=r["sensor_id"],
                kind="viirs_pixel",
                observed_sim_ms=int(r["t_obs_ms"]),
                available_sim_ms=int(r["available_ms"]),
                geometry=Footprint(ring=_ring(r["ring"])),
                hot=True,
                confidence=float(r["confidence"]),
                frp_mw=_none(r["frp_mw"]),
                trust=float(r["trust"]),
                provenance="real",
                source_ref=r["pass_id"],
            )
            for r in recs
        ]
        scan = ScanEvent(
            scan_id=m["entry_id"],
            scenario_id=self.scenario_id,
            sensor_id=sensor_id,
            mode="pass",
            t_start_sim_ms=int(m["scan_start_ms"]),
            t_end_sim_ms=int(m["scan_end_ms"]),
            available_sim_ms=int(m["available_ms"]),
            n_pixels_aoi=len(recs),
            n_hot=len(recs),
            n_clear=0,
            assimilated=sensor_id in self.assimilate,
            provenance=m["provenance"],
        )
        return obs, scan


class InboxSource:
    """Accepted reports waiting for the next cycle."""

    def __init__(self) -> None:
        self._recs: list[ObservationRecord] = []

    def add(self, rec: ObservationRecord) -> None:
        self._recs.append(rec)

    def drain(self, k_ms: int) -> list[ObservationRecord]:
        due = [r for r in self._recs if r.available_sim_ms <= k_ms]
        self._recs = [r for r in self._recs if r.available_sim_ms > k_ms]
        _assert_due([(r.obs_id, r.available_sim_ms) for r in due], k_ms)
        return due

    def clear(self) -> None:
        self._recs.clear()


class SyntheticSource:
    """Tests only: scripted scans of (scan_start_ms, [(lon, lat, hot), ...])."""

    def __init__(self, scans: list[tuple[int, list[tuple[float, float, bool]]]], scenario_id: str = "fixture"):
        self.scenario_id = scenario_id
        self.scans = sorted(scans, key=lambda s: s[0])

    def collect(self, a_ms: int, b_ms: int) -> tuple[list[ObservationRecord], list[ScanEvent]]:
        obs: list[ObservationRecord] = []
        scans: list[ScanEvent] = []
        for start, points in self.scans:
            avail = start + SYN_LATENCY_MS
            if a_ms < avail <= b_ms:
                o, s = self._scan(start, avail, points)
                obs.extend(o)
                scans.append(s)
        _check_window(obs, scans, b_ms)
        return obs, scans

    def _scan(self, start: int, avail: int, points: list[tuple[float, float, bool]]):
        scan_id = f"syn_{pd.Timestamp(start, unit='ms', tz='UTC'):%Y%m%dT%H%M%S}"
        # ponytail: px_x = point index, px_y = 0; keys are stable only if callers keep point order.
        obs = [
            ObservationRecord(
                obs_id=f"obs_{SYN_SENSOR}_{scan_id}_{i}_0",
                scenario_id=self.scenario_id,
                sensor_id=SYN_SENSOR,
                kind="goes_pixel",
                observed_sim_ms=start,
                available_sim_ms=avail,
                geometry=Footprint(ring=_square(lon, lat)),
                hot=hot,
                footprint_key=f"{SYN_SENSOR}:{i}:0",
                provenance="simulated",
                source_ref=scan_id,
            )
            for i, (lon, lat, hot) in enumerate(points)
        ]
        n_hot = sum(1 for p in points if p[2])
        scan = ScanEvent(
            scan_id=scan_id,
            scenario_id=self.scenario_id,
            sensor_id=SYN_SENSOR,
            mode="CONUS",
            t_start_sim_ms=start,
            t_end_sim_ms=avail,
            available_sim_ms=avail,
            n_pixels_aoi=len(points),
            n_hot=n_hot,
            n_clear=len(points) - n_hot,
            provenance="simulated",
        )
        return obs, scan


def _square(lon: float, lat: float) -> list[tuple[float, float]]:
    dlat = SYN_HALF_M / 111_320.0
    dlon = SYN_HALF_M / (111_320.0 * math.cos(math.radians(lat)))
    ring = [(lon - dlon, lat - dlat), (lon + dlon, lat - dlat), (lon + dlon, lat + dlat), (lon - dlon, lat + dlat)]
    return ring + [ring[0]]


def sensor_status(scans_so_far: list[ScanEvent], k_ms: int, enabled: dict[str, bool]) -> list[dict]:
    """SensorInfo deltas (sensor_id, status, last_data_sim_ms) for each sensor in `enabled`."""
    out = []
    for sensor_id, on in enabled.items():
        times = [s.available_sim_ms for s in scans_so_far if s.sensor_id == sensor_id and s.available_sim_ms <= k_ms]
        last = max(times) if times else None
        if not on:
            status = "down"
        elif "goes" in sensor_id and (last is None or last < k_ms - GOES_STALE_MS):
            status = "stale"
        else:
            status = "ok"
        out.append({"sensor_id": sensor_id, "status": status, "last_data_sim_ms": last})
    return out
