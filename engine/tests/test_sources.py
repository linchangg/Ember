"""T7: runtime/sources.py on tiny tables built in tmp_path (spec/03 §3, §14 test 3)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from emberline.contracts import ObservationRecord, PointRadius, ScanEvent
from emberline.runtime.sources import InboxSource, ReplaySource, SyntheticSource, hot_only, sensor_status

T0 = 1_736_301_600_000  # 2025-01-08T02:00:00Z
MIN = 60_000
SCN = "test"
RING = [[-118.1, 34.2], [-118.09, 34.2], [-118.09, 34.21], [-118.1, 34.21], [-118.1, 34.2]]
# (sensor_id, scan_id, scan_start_ms); available = start + 3 min
SCANS = [
    ("goes18_fdcc", "g18c_20250108T020000", T0),
    ("goes18_fdcc", "g18c_20250108T020500", T0 + 5 * MIN),
    ("goes18_fdcc", "g18c_20250108T021000", T0 + 10 * MIN),
    ("goes18_fdcm", "g18m_20250108T021100", T0 + 11 * MIN),
]
# (px_x, px_y, mask, dqf, hot, clear): 2 hot, 1 clear, 1 neither
PIXELS = [(10, 20, 10, 0, True, False), (11, 20, 13, 0, True, False), (12, 20, 100, 1, False, True), (13, 20, 100, 3, False, False)]


def _avail(start: int) -> int:
    return start + 3 * MIN


def _build(scn: Path, doctor: bool = False) -> None:
    (scn / "obs").mkdir(parents=True)
    pix, events, man = [], [], []
    for sensor, scan_id, start in SCANS:
        for j, (x, y, mask, dqf, hot, clear) in enumerate(PIXELS):
            avail = _avail(start) + (MIN if doctor and j == 0 and scan_id == SCANS[0][1] else 0)
            pix.append(dict(scan_id=scan_id, sensor_id=sensor, scan_start_ms=start, scan_end_ms=start + 150_000,
                            created_ms=start + 170_000, s3_last_modified_ms=None, available_ms=avail, px_x=x, px_y=y,
                            lon=-118.095, lat=34.205, ring=RING, mask=mask, dqf=dqf, hot=hot, clear=clear,
                            frp_mw=12.5 if hot else None, trust=0.95 if hot else 1.0))
        events.append(dict(scan_id=scan_id, sensor_id=sensor, mode="CONUS" if sensor.endswith("fdcc") else "MESO",
                           t_start_sim_ms=start, t_end_sim_ms=start + 150_000, available_sim_ms=_avail(start),
                           n_pixels_aoi=3, n_hot=2, n_clear=1, assimilated=True, pixels_path=None, provenance="real"))
        man.append(dict(entry_id=scan_id, source=sensor, kind="observation", scan_start_ms=start,
                        scan_end_ms=start + 150_000, available_ms=_avail(start), latency_basis="product_created",
                        local_path="obs/goes18_fdcc.parquet", rows=4, n_hot=2, provenance="real"))
    man.append(dict(entry_id="hrrr_x", source="hrrr_sfc", kind="forcing", scan_start_ms=T0, scan_end_ms=T0,
                    available_ms=T0 + MIN, latency_basis="s3_last_modified", local_path="forcing/x.nc", rows=0,
                    n_hot=0, provenance="real"))
    pd.DataFrame(pix).to_parquet(scn / "obs" / "goes18_fdcc.parquet")
    pd.DataFrame(events).to_parquet(scn / "obs" / "scan_events.parquet")
    pd.DataFrame(man).to_parquet(scn / "replay_manifest.parquet")


@pytest.fixture
def src(tmp_path: Path) -> ReplaySource:
    _build(tmp_path)
    return ReplaySource(tmp_path, SCN, ["goes18_fdcc"])


def test_window_is_half_open(src: ReplaySource) -> None:
    a, b = _avail(SCANS[0][2]), _avail(SCANS[1][2])  # excludes scan 0 (at a), includes scan 1 (at b)
    obs, scans = src.collect(a, b)
    assert [s.scan_id for s in scans] == [SCANS[1][1]]
    assert {o.source_ref for o in obs} == {SCANS[1][1]}
    assert src.collect(a - 1, a)[1][0].scan_id == SCANS[0][1]


def test_pixels_ids_and_hot_only(src: ReplaySource) -> None:
    obs, _ = src.collect(T0, _avail(SCANS[0][2]))
    assert len(obs) == 3  # the neither-hot-nor-clear pixel is dropped
    assert [o.hot for o in obs] == [True, True, False]
    o = obs[0]
    assert o.obs_id == "obs_goes18_fdcc_g18c_20250108T020000_10_20"
    assert o.footprint_key == "goes18:10:20"
    assert (o.kind, o.fdc_class, o.provenance, o.trust, o.frp_mw) == ("goes_pixel", 10, "real", 0.95, 12.5)
    assert o.observed_sim_ms == T0 and o.available_sim_ms == _avail(T0)
    assert obs[2].frp_mw is None
    assert [h.obs_id for h in hot_only(obs)] == [x.obs_id for x in obs[:2]]


def test_display_only_scan(src: ReplaySource) -> None:
    obs, scans = src.collect(_avail(SCANS[2][2]), _avail(SCANS[3][2]))
    assert obs == []
    assert len(scans) == 1 and scans[0].sensor_id == "goes18_fdcm" and scans[0].assimilated is False


def test_all_scans_once(src: ReplaySource) -> None:
    obs, scans = src.collect(0, T0 + 60 * MIN)
    assert len(scans) == 4 and len(obs) == 9
    assert sum(s.assimilated for s in scans) == 3


def test_future_data_fires_on_doctored_table(tmp_path: Path) -> None:
    _build(tmp_path, doctor=True)
    src = ReplaySource(tmp_path, SCN, ["goes18_fdcc"])
    with pytest.raises(AssertionError, match="future data"):
        src.collect(T0, _avail(T0))


def test_viirs(tmp_path: Path) -> None:
    _build(tmp_path)
    t_obs = T0 + 7 * MIN
    rows = [dict(pass_id="n20_p1", sensor_id="viirs_n20", row=i, t_obs_ms=t_obs, available_ms=t_obs + 2 * MIN,
                 lon=-118.09, lat=34.2, ring=RING, trust=0.8, confidence=0.6, frp_mw=None) for i in range(2)]
    pd.DataFrame(rows).to_parquet(tmp_path / "obs" / "viirs.parquet")
    man = pd.read_parquet(tmp_path / "replay_manifest.parquet")
    man.loc[len(man)] = dict(entry_id="n20_p1", source="viirs_n20", kind="observation", scan_start_ms=t_obs,
                             scan_end_ms=t_obs, available_ms=t_obs + 2 * MIN, latency_basis="assumed_urt",
                             local_path="obs/viirs.parquet", rows=2, n_hot=2, provenance="real")
    man.to_parquet(tmp_path / "replay_manifest.parquet")
    obs, scans = ReplaySource(tmp_path, SCN, ["goes18_fdcc", "viirs_n20"]).collect(t_obs + MIN, t_obs + 2 * MIN)  # after GOES scan 1 at +8 min
    assert [o.obs_id for o in obs] == [f"obs_viirs_n20_{t_obs}_0", f"obs_viirs_n20_{t_obs}_1"]
    assert all(o.kind == "viirs_pixel" and o.hot and o.confidence == 0.6 and o.trust == 0.8 for o in obs)
    assert len(scans) == 1
    s = scans[0]
    assert (s.mode, s.n_hot, s.n_clear, s.n_pixels_aoi, s.assimilated) == ("pass", 2, 0, 2, True)


def _report(i: int, avail: int) -> ObservationRecord:
    return ObservationRecord(obs_id=f"obs_rep_rep_{i}", scenario_id=SCN, sensor_id="report", kind="ground_report",
                             observed_sim_ms=avail, available_sim_ms=avail,
                             geometry=PointRadius(lon=-118.1, lat=34.2, radius_m=200), hot=True, provenance="injected")


def test_inbox() -> None:
    inbox = InboxSource()
    inbox.add(_report(1, T0))
    inbox.add(_report(2, T0 + 10 * MIN))
    assert [r.obs_id for r in inbox.drain(T0 + 5 * MIN)] == ["obs_rep_rep_1"]
    assert inbox.drain(T0 + 5 * MIN) == []
    inbox.clear()
    assert inbox.drain(T0 + 60 * MIN) == []


def test_synthetic_roundtrip() -> None:
    syn = SyntheticSource([(T0, [(-118.09, 34.2, True), (-118.1, 34.21, False)]), (T0 + 5 * MIN, [(-118.09, 34.2, True)])])
    obs, scans = syn.collect(T0, T0 + 3 * MIN)
    assert len(obs) == 2 and len(scans) == 1
    for o in obs:
        assert ObservationRecord.model_validate(o.model_dump()) == o
        assert o.provenance == "simulated" and o.available_sim_ms == T0 + 3 * MIN
        assert o.geometry.ring[0] == o.geometry.ring[-1]
    assert obs[0].obs_id == "obs_synthetic_goes_syn_20250108T020000_0_0"
    assert ScanEvent.model_validate(scans[0].model_dump()) == scans[0]
    assert (scans[0].n_hot, scans[0].n_clear) == (1, 1)
    assert syn.collect(T0 + 3 * MIN, T0 + 8 * MIN)[1][0].t_start_sim_ms == T0 + 5 * MIN


def test_sensor_status() -> None:
    scan = SyntheticSource([(T0, [(-118.09, 34.2, True)])], SCN).collect(0, T0 + 3 * MIN)[1][0]
    goes = scan.model_copy(update={"sensor_id": "goes18_fdcc"})
    viirs = scan.model_copy(update={"sensor_id": "viirs_n20", "mode": "pass"})
    on = {"goes18_fdcc": True, "viirs_n20": True}
    t = goes.available_sim_ms

    def st(k: int, enabled: dict[str, bool] = on) -> dict[str, tuple]:
        return {d["sensor_id"]: (d["status"], d["last_data_sim_ms"]) for d in sensor_status([goes, viirs], k, enabled)}

    assert st(t + 15 * MIN) == {"goes18_fdcc": ("ok", t), "viirs_n20": ("ok", t)}
    assert st(t + 15 * MIN + 1)["goes18_fdcc"] == ("stale", t)
    assert st(t + 60 * MIN)["viirs_n20"] == ("ok", t)
    assert st(t, {"goes18_fdcc": False, "viirs_n20": True})["goes18_fdcc"][0] == "down"
    assert st(t - 1)["goes18_fdcc"] == ("stale", None)
