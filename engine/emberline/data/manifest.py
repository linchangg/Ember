"""Replay manifest + GOES latency log (spec/01 §8). Columns: runtime/sources.py docstring.

    uv run python -m emberline.data.manifest --scenario eaton [--rebuild-goes]

Reads obs/goes18_fdcc.parquet + obs/scan_events.parquet (data/goes.py, built here if missing)
and forcing/hrrr_*.nc; writes replay_manifest.parquet and truth/latency.json.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from emberline.data import goes
from emberline.data.config import load_scenario, scenario_dir, utc_ms

GOES_TABLE = "obs/goes18_fdcc.parquet"
HRRR_SOURCE = "hrrr_sfc"
HOUR_MS = 3_600_000
MAX_GAP_MS = 15 * 60_000  # GOES "stale" threshold (runtime/sources.py)
COLUMNS = ["entry_id", "source", "kind", "scan_start_ms", "scan_end_ms", "available_ms", "latency_basis",
           "local_path", "rows", "n_hot", "provenance"]


def goes_rows(events: pd.DataFrame, basis: dict[str, str]) -> pd.DataFrame:
    """One observation row per scan (zero-row scans included: an all-cloud scan is still a scan)."""
    return pd.DataFrame({
        "entry_id": events["scan_id"],
        "source": events["sensor_id"],
        "kind": "observation",
        "scan_start_ms": events["t_start_sim_ms"],
        "scan_end_ms": events["t_end_sim_ms"],
        "available_ms": events["available_sim_ms"],
        "latency_basis": events["scan_id"].map(basis),
        "local_path": GOES_TABLE,
        "rows": events["n_pixels_aoi"],
        "n_hot": events["n_hot"],
        "provenance": events["provenance"],
    })


def hrrr_basis(init_ms: int, avail0: int) -> str:
    """hrrr.available_ms fallback for fxx 0 is init + 60 min; anything else came from LastModified.

    01 §8's latency_basis enum has no HRRR-assumed value, so a fallback init stops the build instead of
    writing an off-contract label (flagged to Cody). ponytail: inferred from the value, since hrrr.py
    stores no per-fxx basis; an LM landing exactly on init + 60 min would trip this too.
    """
    assert avail0 != init_ms + HOUR_MS, f"HRRR init {init_ms}: assumed availability; 01 §8 enum has no label for it"
    return "s3_last_modified"


def hrrr_rows(scn_dir: Path) -> pd.DataFrame:
    """One forcing row per init; available_ms = fxx-0's availability (later fxx bands arrive later)."""
    rows = []
    for p in sorted((scn_dir / "forcing").glob("hrrr_*.nc")):
        with xr.open_dataset(p) as ds:
            init_ms = int(ds.attrs["init_ms"])
            avail = np.atleast_1d(ds.attrs["available_ms_by_fxx"]).astype(np.int64)
            n_fxx = int(ds.sizes["fxx"])
        rows.append({
            "entry_id": p.stem, "source": HRRR_SOURCE, "kind": "forcing",
            "scan_start_ms": init_ms, "scan_end_ms": init_ms + (n_fxx - 1) * HOUR_MS,  # init / last valid time
            "available_ms": int(avail[0]), "latency_basis": hrrr_basis(init_ms, int(avail[0])),
            "local_path": p.relative_to(scn_dir).as_posix(), "rows": 0, "n_hot": 0, "provenance": "real",
        })
    return pd.DataFrame(rows, columns=COLUMNS)


def build_manifest(events: pd.DataFrame, basis: dict[str, str], scn_dir: Path) -> pd.DataFrame:
    assert set(events["scan_id"]) <= set(basis), "scan without a latency basis"
    parts = [goes_rows(events, basis), hrrr_rows(scn_dir)]
    man = pd.concat([p for p in parts if len(p)], ignore_index=True)[COLUMNS]
    man = man.astype({"scan_start_ms": "int64", "scan_end_ms": "int64", "available_ms": "int64",
                      "rows": "int64", "n_hot": "int64"})
    return man.sort_values(["available_ms", "entry_id"], kind="stable", ignore_index=True)


def check_manifest(man: pd.DataFrame, start_ms: int, end_ms: int) -> None:
    """01 §12 test 7: sorted by available_ms; GOES covers start..end with no gap > 15 min; forcing ready at start."""
    assert man["available_ms"].is_monotonic_increasing, "manifest not sorted by available_ms"
    assert man["entry_id"].is_unique, "duplicate entry_id"
    obs = man[man["kind"] == "observation"]
    assert (obs["available_ms"] >= obs["scan_end_ms"]).all(), "observation available before scan end"
    f = man[man["kind"] == "forcing"]
    assert (f["available_ms"] >= f["scan_start_ms"]).all(), "forcing available before init"
    t = man.loc[man["source"] == goes.SENSOR_ID, "available_ms"].to_numpy()
    assert t.size and t[0] <= start_ms + MAX_GAP_MS and t[-1] >= end_ms, "GOES does not cover the replay window"
    inside = t[(t >= start_ms) & (t <= end_ms)]
    assert np.diff(np.concatenate([[start_ms], inside, [end_ms]])).max() <= MAX_GAP_MS, "GOES gap > 15 min"
    if len(f):
        assert f["available_ms"].min() <= start_ms, "no HRRR init available at replay start"


def latency(events: pd.DataFrame, man: pd.DataFrame) -> dict:
    lat_min = (events["available_sim_ms"] - events["t_end_sim_ms"]) / 60_000
    basis = man.loc[man["source"] == goes.SENSOR_ID, "latency_basis"].value_counts().to_dict()
    return {
        "sensor_id": goes.SENSOR_ID,
        "metric": "available_ms - scan_end_ms, minutes",
        "rule": "max(created, s3_last_modified) if s3_last_modified - scan_end < 2 h, else created + 3 min (01 §8)",
        "n_scans": int(len(lat_min)),
        "median_min": round(float(lat_min.median()), 3),
        "p90_min": round(float(lat_min.quantile(0.9)), 3),
        "min_min": round(float(lat_min.min()), 3),
        "max_min": round(float(lat_min.max()), 3),
        "latency_basis_counts": {str(k): int(v) for k, v in basis.items()},
        "note": "S3 LastModified as stored in noaa-goes18 today; a later re-upload would move it (guarded by the 2 h rule).",
        "provenance": "real",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="eaton")
    ap.add_argument("--rebuild-goes", action="store_true", help="rebuild the GOES pixel table first")
    args = ap.parse_args()
    scn_dir = scenario_dir(args.scenario)
    if args.rebuild_goes or not (scn_dir / GOES_TABLE).exists():
        goes.build_pixel_table(args.scenario)
    events = pd.read_parquet(scn_dir / "obs" / "scan_events.parquet")
    basis = {s["scan_id"]: s["latency_basis"] for s in goes.scan_index(args.scenario)}
    man = build_manifest(events, basis, scn_dir)
    cfg = load_scenario(args.scenario)
    check_manifest(man, utc_ms(cfg["replay_start_utc"]), utc_ms(cfg["replay_end_utc"]))
    man.to_parquet(scn_dir / "replay_manifest.parquet", index=False)
    lat = latency(events, man)
    (scn_dir / "truth").mkdir(parents=True, exist_ok=True)
    (scn_dir / "truth" / "latency.json").write_text(json.dumps(lat, indent=2) + "\n", encoding="utf-8")
    print(man.groupby(["source", "latency_basis"]).size().to_string())
    print(f"{len(man)} manifest rows -> {scn_dir / 'replay_manifest.parquet'}")
    print(f"GOES latency: median {lat['median_min']} min, p90 {lat['p90_min']} min (n={lat['n_scans']})")


if __name__ == "__main__":
    main()
