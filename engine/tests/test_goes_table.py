"""GOES-18 pixel table + scan events (01 §3, §8; §12 test 4). Real-data tests skip until T8's build has run."""

import pandas as pd
import pytest

from ember.contracts import GOES_FIRE
from ember.data import goes
from ember.data.config import scenario_dir

OBS = scenario_dir("eaton") / "obs"
needs_table = pytest.mark.skipif(not (OBS / "goes18_fdcc.parquet").exists(), reason="run python -m ember.data.goes")
COLS = ["scan_id", "sensor_id", "scan_start_ms", "scan_end_ms", "created_ms", "s3_last_modified_ms", "available_ms",
        "px_x", "px_y", "lon", "lat", "ring", "mask", "dqf", "hot", "clear", "frp_mw", "trust"]
EV_COLS = ["scan_id", "sensor_id", "mode", "t_start_sim_ms", "t_end_sim_ms", "available_sim_ms", "n_pixels_aoi",
           "n_hot", "n_clear", "assimilated", "pixels_path", "provenance"]


def test_classify_rules():
    assert goes.classify(30, 2) is None and goes.classify(100, 5) is None and goes.classify(100, 255) is None
    assert goes.classify(10, 0) == (True, False, 0.95)
    assert goes.classify(33, 0) == (True, False, 0.85)
    assert goes.classify(14, 0) == (True, False, 0.6)
    assert goes.classify(35, 0) == (True, False, 0.3)
    assert goes.classify(100, 1) == (False, True, 1.0)


def test_availability_rule():
    end, created = 1_000_000, 1_020_000
    assert goes.availability(created, end, 1_040_000) == (1_040_000, "s3_last_modified")
    assert goes.availability(created, end, 1_010_000) == (created, "s3_last_modified")  # max(created, LM)
    late = end + 2 * 3_600_000
    assert goes.availability(created, end, late) == (created + 180_000, "product_created")
    assert goes.availability(created, end, None) == (created + 180_000, "product_created")


def test_scan_id_and_key():
    name = "OR_ABI-L2-FDCC-M6_G18_s20250080201172_e20250080203545_c20250080204125.nc"
    assert goes.scan_id(goes.parse_times(name)["scan_start_ms"]) == "g18c_20250108T020117"
    assert goes.s3_key(name) == f"noaa-goes18/ABI-L2-FDCC/2025/008/02/{name}"


def test_fd_index_integer_grid():
    col, row = goes.fd_index([-0.151844, 0.045108], [0.151844, 0.096068])
    assert col.tolist() == [0, 3517] and row.tolist() == [0, 996]
    with pytest.raises(AssertionError):
        goes.fd_index([0.000028], [0.0])


@needs_table
def test_pixel_table_rules():  # 01 §12 test 4
    px = pd.read_parquet(OBS / "goes18_fdcc.parquet")
    assert list(px.columns) == COLS
    assert not px["dqf"].isin([2, 3, 4, 5]).any()
    assert (px["hot"] == px["mask"].isin(GOES_FIRE | goes.GOES_LOW)).all()
    assert (px["clear"] == (px["dqf"] == 1)).all()
    assert (px["available_ms"] >= px["scan_end_ms"]).all()
    assert (px.loc[px["hot"], "trust"] == px.loc[px["hot"], "mask"].map(goes.TRUST).astype("float32")).all()
    assert px["scan_id"].str.fullmatch(r"g18c_\d{8}T\d{6}").all()
    assert not px.duplicated(["scan_id", "px_x", "px_y"]).any()
    ring = px["ring"].iloc[0]
    assert len(ring) == 5 and list(ring[0]) == list(ring[-1])  # closed
    assert px["lon"].between(-118.4, -117.8).all() and px["lat"].between(33.9, 34.5).all()


@needs_table
def test_scan_events_match_rows():
    px = pd.read_parquet(OBS / "goes18_fdcc.parquet")
    ev = pd.read_parquet(OBS / "scan_events.parquet").set_index("scan_id")
    assert list(pd.read_parquet(OBS / "scan_events.parquet").columns) == EV_COLS
    assert (ev["mode"] == "CONUS").all() and (ev["sensor_id"] == "goes18_fdcc").all()
    g = px.groupby("scan_id")
    n = g.size().reindex(ev.index, fill_value=0)
    assert (ev["n_pixels_aoi"] == n).all()
    assert (ev["n_hot"] == g["hot"].sum().reindex(ev.index, fill_value=0)).all()
    assert (ev["n_clear"] == g["clear"].sum().reindex(ev.index, fill_value=0)).all()
    assert (ev["available_sim_ms"] >= ev["t_end_sim_ms"]).all()
