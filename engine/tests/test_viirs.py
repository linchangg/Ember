"""VIIRS pixel table (01 §4) + its manifest rows. Real-data tests skip until data/viirs.py has run."""

import pandas as pd
import pytest

from emberline.data import viirs
from emberline.data.config import load_scenario, scenario_dir
from emberline.data.grid import bbox_lonlat
from emberline.runtime.sources import ReplaySource

SCN = scenario_dir("eaton")
pytestmark = pytest.mark.skipif(not (SCN / "obs" / "viirs.parquet").exists(),
                                reason="run python -m emberline.data.viirs")


@pytest.fixture(scope="module")
def table() -> pd.DataFrame:
    return pd.read_parquet(SCN / "obs" / "viirs.parquet")


def test_table(table):
    assert list(table.columns) == viirs.COLUMNS and len(table)
    assert (table["available_ms"] >= table["t_obs_ms"]).all()
    w, s, e, n = bbox_lonlat(load_scenario("eaton")["grid"])
    assert table["lon"].between(w, e).all() and table["lat"].between(s, n).all()
    assert set(table["sensor_id"]) <= {"viirs_snpp", "viirs_n20", "viirs_n21"}
    assert set(table["confidence"].astype(float).round(2)) <= {0.6, 0.9}  # nominal/high only
    assert all(r[0][0] == r[-1][0] and r[0][1] == r[-1][1] for r in table["ring"])
    assert not table.duplicated(["sensor_id", "t_obs_ms", "row"]).any()  # obs_id is unique


def test_no_persistent_anomalies(table):
    """Every raw detection kept has FP_PersistentAnomalyCategory 0; the static pixel near -118.194, 34.153 is gone."""
    import netCDF4

    flagged = set()
    for p in (SCN / "raw" / "viirs").glob("EFIRE-*.nc"):
        with netCDF4.Dataset(p) as nc:
            nc.set_auto_mask(False)
            pa, lon, lat = (nc.variables[k][:] for k in ("FP_PersistentAnomalyCategory", "FP_Longitude", "FP_Latitude"))
        flagged |= {(round(float(x), 4), round(float(y), 4)) for x, y, c in zip(lon, lat, pa) if c > 0}
    kept = set(zip(table["lon"].round(4), table["lat"].round(4)))
    assert not kept & flagged
    assert not ((table["lon"] - -118.194).abs().lt(0.005) & (table["lat"] - 34.153).abs().lt(0.005)).any()


def test_manifest_rows_match(table):
    man = pd.read_parquet(SCN / "replay_manifest.parquet")
    v = man[man["source"].str.startswith("viirs")].set_index("entry_id").sort_index()
    g = table.groupby("pass_id")
    assert list(v.index) == sorted(g.groups)
    assert (v["rows"] == g.size()).all() and (v["n_hot"] == v["rows"]).all()
    assert (v["available_ms"] == g["available_ms"].max()).all()
    assert (v["scan_start_ms"] == g["t_obs_ms"].min()).all() and (v["scan_end_ms"] == g["t_obs_ms"].max()).all()
    assert (v["source"] == g["sensor_id"].first()).all()
    assert set(v["latency_basis"]) == {viirs.LATENCY_BASIS} and set(v["local_path"]) == {"obs/viirs.parquet"}
    assert set(v["kind"]) == {"observation"} and set(v["provenance"]) == {"real"}


def test_replay_releases_viirs_only_when_available(table):
    src = ReplaySource(SCN, "eaton", ["goes18_fdcc", "viirs_snpp", "viirs_n20", "viirs_n21"])
    for pid, g in table.groupby("pass_id"):
        avail = int(g["available_ms"].max())
        before, _ = src.collect(int(g["t_obs_ms"].min()) - 1, avail - 1)
        assert not [o for o in before if o.source_ref == pid]
        obs, scans = src.collect(avail - 1, avail)
        mine = [o for o in obs if o.source_ref == pid]
        assert len(mine) == len(g) and all(o.kind == "viirs_pixel" and o.available_sim_ms <= avail for o in mine)
        assert [s.mode for s in scans if s.scan_id == pid] == ["pass"]
