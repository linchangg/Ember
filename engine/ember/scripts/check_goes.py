"""G1-a GOES go/no-go check (spec/01 §2). Reads archived GOES-18 FDCC for the Eaton night.

uv run python -m ember.scripts.check_goes --scenario eaton [--hours 2-15] [--no-fdcm]
"""

from __future__ import annotations

import argparse
import base64
import html
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from matplotlib.patches import Polygon as MplPolygon  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402
from pyproj import Transformer  # noqa: E402

from ember.contracts import GOES_FIRE, GOES_STRONG  # noqa: E402
from ember.data.area_x import area_x_box, area_x_satellite_hit, lake_ave_lon, t_sat_goes  # noqa: E402
from ember.data.config import load_scenario, scenario_dir, utc_ms  # noqa: E402
from ember.data.goes import _open, correct_pixel, footprint_ring, list_fdc, parse_times, read_crop  # noqa: E402
from ember.data.grid import bbox_lonlat  # noqa: E402

PST = timezone(timedelta(hours=-8))
DEADLINE_ISO = "2025-01-08T11:25:00Z"  # 03:25 PST evacuation-order time (01 §2 step 6)
DQF_NO_INFO = (2, 3, 4, 5)
CLASS_COLOR = {  # research 01 §2 mask table
    "high": ((10, 11, 30, 31), "#d7191c"),
    "medium": ((12, 13, 32, 33), "#fdae61"),
    "low": ((14, 34), "#ffff66"),
}


def pst(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, PST).strftime("%H:%M")


def utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%H:%MZ")


def mask_color(m: int) -> str:
    return next(c for codes, c in CLASS_COLOR.values() if m in codes)


# --- DEM for parallax (T1 step 4) ------------------------------------------------------------


def ensure_dem(scn: str, bbox: tuple) -> tuple[Path | None, str]:
    """Abby's static/dem.tif if present, else a 90 m 3DEP pull cached under raw/."""
    static = scenario_dir(scn) / "static" / "dem.tif"
    if static.exists():
        return static, f"static/dem.tif (A1): {static}"
    raw = scenario_dir(scn) / "raw" / "dem_parallax.tif"
    if not raw.exists():
        try:
            import py3dep

            w, s, e, n = bbox
            pad = 0.1  # apparent GOES positions sit up to ~3 km NE of the truth, plus the 5 km crop margin
            dem = py3dep.get_dem((w - pad, s - pad, e + pad, n + pad), resolution=90)
            raw.parent.mkdir(parents=True, exist_ok=True)
            dem.rio.to_raster(raw)
        except Exception as ex:  # noqa: BLE001
            print(f"WARNING: py3dep DEM failed ({type(ex).__name__}: {ex})")
            return None, "WARNING: constant parallax elevation 600 m (no DEM available)"
    return raw, f"USGS 3DEP 90 m via py3dep.get_dem: {raw}"


def dem_lookup_fn(path: Path | None):
    if path is None:
        print("WARNING: constant parallax elevation (600 m)")
        return lambda lon, lat: 600.0
    with rasterio.open(path) as src:
        arr = src.read(1).astype(np.float64)
        nodata = src.nodata
        tf = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
        index = src.index
    arr[~np.isfinite(arr)] = 0.0
    if nodata is not None:
        arr[arr == nodata] = 0.0

    def lookup(lon: float, lat: float) -> float:
        r, c = index(*tf.transform(lon, lat))
        r, c = min(max(r, 0), arr.shape[0] - 1), min(max(c, 0), arr.shape[1] - 1)
        return float(arr[r, c])

    return lookup


# --- per-scan processing ---------------------------------------------------------------------


def scan_pixels(key: str, bbox: tuple, dem_lookup) -> dict:
    c = read_crop(key, bbox)
    mask, dqf = c["mask"].astype(int), c["dqf"].astype(int)
    keep = (np.isin(mask, list(GOES_FIRE)) | (dqf == 1)) & ~np.isin(dqf, DQF_NO_INFO)
    fire_idx = np.argwhere(keep & np.isin(mask, list(GOES_FIRE)))
    w, s, e, n = bbox
    fire = []
    for i, j in fire_idx:
        ring = footprint_ring(c["x"][j], c["y"][i], c["geos"], c["h"])
        lon, lat, ring_c = correct_pixel(float(c["lon"][i, j]), float(c["lat"][i, j]), ring, dem_lookup)
        if not (w <= lon <= e and s <= lat <= n):
            continue  # crop margin only; counts are for the model bbox
        fire.append({"mask": int(mask[i, j]), "dqf": int(dqf[i, j]), "lon": lon, "lat": lat,
                     "ring": ring_c, "frp_mw": float(c["power"][i, j])})
    return {
        "key": key,
        "t": c["scan_start_ms"],
        "pixels": fire,
        "hit": None,
        "n_fire": len(fire),
        "n_strong": sum(p["mask"] in GOES_STRONG for p in fire),
        "n_clear": int((dqf == 1).sum()),
        "n_noinfo": int(np.isin(dqf, DQF_NO_INFO).sum()),
    }


def fdcm_coverage(hours: range, bbox: tuple) -> list[str]:
    """Informational (01 §2 (d)): which mesoscale sectors covered the bbox. Not ingested."""
    keys = list_fdc("ABI-L2-FDCM", 8, hours)
    by_sector: dict[str, list[str]] = {}
    for k in keys:
        by_sector.setdefault(k.rsplit("/", 1)[-1].split("_")[1], []).append(k)
    w, s, e, n = bbox
    out = []
    for sector, ks in sorted(by_sector.items()):
        # sample first/middle/last: mesoscale sectors move during the night
        for k in sorted({ks[0], ks[len(ks) // 2], ks[-1]}):
            a = _open(k)["geospatial_lat_lon_extent"].attrs
            W, E = float(a["geospatial_westbound_longitude"]), float(a["geospatial_eastbound_longitude"])
            S, N = float(a["geospatial_southbound_latitude"]), float(a["geospatial_northbound_latitude"])
            cov = W <= w and E >= e and S <= s and N >= n
            t = parse_times(k)["scan_start_ms"]
            out.append(f"{sector} ({len(ks)} files) @ {utc(t)}: lon {W:.2f}..{E:.2f}, lat {S:.2f}..{N:.2f}"
                       f" -> {'COVERS' if cov else 'does not cover'} bbox")
    return out


# --- report ----------------------------------------------------------------------------------


def png_b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=60, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def draw_context(ax, cfg: dict, bbox: tuple) -> None:
    lo0, la0, lo1, la1 = area_x_box(cfg)
    ax.add_patch(Rectangle((lo0, la0), lo1 - lo0, la1 - la0, fill=False, ec="#2b83ba", lw=1.5))
    ax.axvline(lake_ave_lon(cfg), color="#2b83ba", ls="--", lw=1)
    ig = cfg["ignition"]
    ax.plot(ig["lon"], ig["lat"], "k*", ms=8)
    ax.set_xlim(bbox[0], bbox[2])
    ax.set_ylim(bbox[1], bbox[3])
    ax.set_aspect(1 / np.cos(np.radians((bbox[1] + bbox[3]) / 2)))


def area_x_png(dem_path: Path | None, cfg: dict, bbox: tuple) -> str:
    fig, ax = plt.subplots(figsize=(8, 8))
    if dem_path is not None:
        import rioxarray

        da = rioxarray.open_rasterio(dem_path).squeeze(drop=True).rio.reproject("EPSG:4326")  # 3DEP comes in EPSG:5070
        z = da.values.astype(np.float64)
        left, bottom, right, top = da.rio.bounds()
        z[~np.isfinite(z) | (z < -1000)] = 0
        gy, gx = np.gradient(z)
        hs = np.clip(0.7 - 0.02 * gx + 0.02 * gy, 0, 1)  # cheap NW-lit hillshade
        ax.imshow(hs, cmap="gray", extent=(left, right, bottom, top), origin="upper")
        cs = ax.contour(np.linspace(left, right, z.shape[1]), np.linspace(top, bottom, z.shape[0]),
                        z, levels=[300, 500, 800, 1200], colors="#8c510a", linewidths=0.6)
        ax.clabel(cs, fontsize=7)
    draw_context(ax, cfg, bbox)
    ax.set_title("Area X (blue box), Lake Ave meridian (dashed), ignition (*); DEM contours in m")
    return png_b64(fig)


def frames_png(frames: list[dict], cfg: dict, bbox: tuple) -> str:
    cols = 8
    rows = -(-len(frames) // cols)
    fig, axes = plt.subplots(rows, cols, figsize=(3 * cols, 3 * rows), squeeze=False)
    for ax in axes.flat:
        ax.set_xticks([])
        ax.set_yticks([])
    for ax, f in zip(axes.flat, frames):
        for p in f["pixels"]:
            ax.add_patch(MplPolygon(p["ring"], closed=True, fc=mask_color(p["mask"]), ec="k", lw=0.4, alpha=0.8))
        draw_context(ax, cfg, bbox)
        ax.set_title(f"{pst(f['t'])} PST{'  HIT' if f['hit'] else ''}", fontsize=10,
                     color="#d7191c" if f["hit"] else "k")
    return png_b64(fig)


def write_report(path: Path, ctx: dict) -> None:
    rows = "".join(
        f"<tr{' class=hit' if r['hit'] else ''}><td>{utc(r['t'])}</td><td>{pst(r['t'])}</td>"
        f"<td>{'HIT' if r['hit'] else ''}</td><td>{r['n_fire']}</td><td>{r['n_strong']}</td>"
        f"<td>{r['n_clear']}</td><td>{r['n_noinfo']}</td></tr>"
        for r in ctx["scans"]
    )
    summ = "".join(f"<tr><th>{html.escape(k)}</th><td>{html.escape(str(v))}</td></tr>" for k, v in ctx["summary"].items())
    fdcm = "".join(f"<li>{html.escape(x)}</li>" for x in ctx["fdcm"]) or "<li>skipped (--no-fdcm)</li>"
    legend = " ".join(f"<span style='background:{c};padding:0 6px'>{k} {codes}</span>" for k, (codes, c) in CLASS_COLOR.items())
    path.write_text(f"""<!doctype html><html><head><meta charset="utf-8"><title>G1-a GOES check</title>
<style>body{{font-family:sans-serif;margin:16px}}table{{border-collapse:collapse}}td,th{{border:1px solid #ccc;padding:2px 8px;text-align:left}}
tr.hit{{background:#fdd}}img{{max-width:100%}}</style></head><body>
<h1>G1-a: GOES-18 FDCC check, Eaton</h1>
<p><b>{html.escape(ctx['decision'])}</b></p>
<h2>Area X sanity</h2><img src="data:image/png;base64,{ctx['area_png']}">
<h2>Summary</h2><table>{summ}</table>
<h3>(d) FDCM coverage (informational)</h3><ul>{fdcm}</ul>
<h2>Frames every 15 min (parallax-corrected footprints)</h2><p>{legend}</p>
<img src="data:image/png;base64,{ctx['frames_png']}">
<h2>(c) Per-scan persistence</h2>
<p>Counts are for corrected fire pixels inside the model bbox; n_clear = DQF 1 and n_noinfo = DQF 2-5 in the crop window.</p>
<table><tr><th>scan start</th><th>PST</th><th>area-X hit</th><th>n_fire</th><th>n_strong</th><th>n_clear</th><th>n_noinfo</th></tr>{rows}</table>
</body></html>""", encoding="utf-8")


def parse_hours(s: str) -> range:
    a, b = (int(v) for v in s.split("-"))
    return range(a, b + 1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="eaton")
    ap.add_argument("--hours", default="2-15", help="UTC hours on day 008, inclusive")
    ap.add_argument("--no-fdcm", action="store_true")
    args = ap.parse_args()

    cfg = load_scenario(args.scenario)
    bbox = bbox_lonlat(cfg["grid"])
    hours = parse_hours(args.hours)
    print(f"bbox {bbox}, area X {area_x_box(cfg)}, Lake Ave lon {lake_ave_lon(cfg)}")

    dem_path, dem_src = ensure_dem(args.scenario, bbox)
    print(f"DEM for parallax: {dem_src}")
    dem_lookup = dem_lookup_fn(dem_path)

    keys = list_fdc("ABI-L2-FDCC", 8, hours)
    print(f"{len(keys)} FDCC files, hours {hours.start:02d}-{hours.stop - 1:02d}Z")
    scans = []
    for k in keys:
        r = scan_pixels(k, bbox, dem_lookup)
        r["hit"] = area_x_satellite_hit(r["pixels"], cfg)
        scans.append(r)
        print(f"  {utc(r['t'])} ({pst(r['t'])} PST) fire={r['n_fire']:3d} strong={r['n_strong']:3d}"
              f" clear={r['n_clear']:4d}{'  HIT' if r['hit'] else ''}")
    scans.sort(key=lambda r: r["t"])

    ign = utc_ms(cfg["t_ignition_utc"])
    first = next((r["t"] for r in scans if r["t"] >= ign and r["n_fire"] > 0), None)
    tsat = t_sat_goes([(r["t"], r["hit"]) for r in scans])
    fdcm = [] if args.no_fdcm else fdcm_coverage(hours, bbox)
    for line in fdcm:
        print(f"FDCM {line}")

    if tsat is not None and tsat < utc_ms(DEADLINE_ISO):
        decision = f"G1-a: GO (Eaton), t_sat_goes = {pst(tsat)} PST"
    else:
        decision = "G1-a: GOES NO-GO; run the VIIRS check (01 §4) before switching to Palisades"
    summary = {
        "(a) first fire detection after 02:15Z": f"{utc(first)} / {pst(first)} PST" if first is not None else "none",
        "(b) t_sat_goes": f"{utc(tsat)} / {pst(tsat)} PST" if tsat is not None else "none",
        "scans with area-X hit": sum(r["hit"] for r in scans),
        "scans read": len(scans),
        "DEM source for parallax": dem_src,
        "deadline": f"{DEADLINE_ISO} (03:25 PST)",
    }
    frames = scans[::3]  # FDCC is every 5 min -> one frame per 15 min
    out = scenario_dir(args.scenario) / "check_goes_report.html"
    write_report(out, {
        "decision": decision, "summary": summary, "scans": scans, "fdcm": fdcm,
        "area_png": area_x_png(dem_path, cfg, bbox), "frames_png": frames_png(frames, cfg, bbox),
    })
    for k, v in summary.items():
        print(f"{k}: {v}")
    print(f"report: {out}")
    print(decision)


if __name__ == "__main__":
    main()
