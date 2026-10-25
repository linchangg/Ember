"""Replay manifest (01 §8; §12 test 7) + ReplaySource round trip. Real-data tests skip until T8's build has run."""

import json
import re

import pandas as pd
import pytest

from ember.data.config import load_scenario, scenario_dir, utc_ms
from ember.data.manifest import COLUMNS, check_manifest
from ember.runtime.sources import ReplaySource

SCN = scenario_dir("eaton")
needs_manifest = pytest.mark.skipif(not (SCN / "replay_manifest.parquet").exists(),
                                    reason="run python -m ember.data.manifest")
START, END = utc_ms("2025-01-08T02:00:00Z"), utc_ms("2025-01-08T15:00:00Z")


def _goes(ts: list[int]) -> pd.DataFrame:
    return pd.DataFrame([{"entry_id": f"s{i}", "source": "goes18_fdcc", "kind": "observation", "scan_start_ms": t - 60_000,
                          "scan_end_ms": t - 30_000, "available_ms": t} for i, t in enumerate(ts)])


def test_check_manifest_catches_gaps_and_order():
    every5 = list(range(START + 240_000, END + 300_000, 300_000))
    check_manifest(_goes(every5), START, END)
    with pytest.raises(AssertionError, match="gap"):
        check_manifest(_goes([t for t in every5 if not START + 3_600_000 < t < START + 5_400_000]), START, END)
    with pytest.raises(AssertionError, match="sorted"):
        check_manifest(_goes(every5[::-1]), START, END)
    with pytest.raises(AssertionError, match="cover"):
        check_manifest(_goes([t for t in every5 if t < END - 600_000]), START, END)


@needs_manifest
def test_manifest_sorted_and_covers():  # 01 §12 test 7
    man = pd.read_parquet(SCN / "replay_manifest.parquet")
    assert list(man.columns) == COLUMNS
    cfg = load_scenario("eaton")
    check_manifest(man, utc_ms(cfg["replay_start_utc"]), utc_ms(cfg["replay_end_utc"]))
    obs = man[man["kind"] == "observation"]
    ev = pd.read_parquet(SCN / "obs" / "scan_events.parquet")
    goes_obs = obs[~obs["source"].str.startswith("viirs")]
    assert sorted(goes_obs["entry_id"]) == sorted(ev["scan_id"])  # one row per GOES scan; VIIRS: test_viirs.py
    assert set(man["kind"]) <= {"observation", "forcing"}
    assert set(man["latency_basis"]) <= {"s3_last_modified", "product_created", "assumed_urt", "assumed_nrt"}  # 01 §8 enum


@needs_manifest
def test_latency_json():
    lat = json.loads((SCN / "truth" / "latency.json").read_text(encoding="utf-8"))
    assert 0 <= lat["median_min"] <= lat["p90_min"] and lat["n_scans"] > 0


@needs_manifest
def test_replay_source_round_trip():
    src = ReplaySource(SCN, "eaton", ["goes18_fdcc"])
    a, b = utc_ms("2025-01-08T09:00:00Z"), utc_ms("2025-01-08T10:00:00Z")
    obs, scans = src.collect(a, b)
    assert all(a < s.available_sim_ms <= b for s in scans)
    scans = [s for s in scans if s.mode == "CONUS"]  # VIIRS passes report too, unassimilated here
    assert len(scans) == 12 and obs
    for o in obs:
        assert a < o.available_sim_ms <= b and o.observed_sim_ms < o.available_sim_ms
        assert re.fullmatch(r"obs_goes18_fdcc_g18c_\d{8}T\d{6}_\d+_\d+", o.obs_id)
        assert re.fullmatch(r"goes18:\d+:\d+", o.footprint_key)
        assert o.geometry.ring[0] == o.geometry.ring[-1]
    assert any(o.hot for o in obs) and any(not o.hot for o in obs)
