"""Import independently mapped perimeter + explicit coverage on the evaluation grid.

Never downloads data or treats final perimeters as earlier truth. The caller must
review source timestamps and independence; metadata alone cannot establish either.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from pyproj import CRS, Transformer
from rasterio.features import rasterize
from rasterio.transform import from_origin
from shapely.geometry import shape
from shapely import transform
from shapely.ops import unary_union

from .historical import Grid, digest, grid_digest


def polygons(path: Path, source_crs: str, grid: Grid):
    data = json.loads(path.read_text())
    objects = (
        [f["geometry"] for f in data["features"]]
        if data["type"] == "FeatureCollection"
        else [data["geometry"]]
        if data["type"] == "Feature"
        else [data]
    )
    if not objects:
        raise ValueError("Explicit polygon geometry is required")
    project = Transformer.from_crs(
        CRS.from_user_input(source_crs), grid.crs, always_xy=True
    ).transform
    projected = []
    for obj in objects:
        geom = shape(obj)
        if (
            geom.geom_type not in ("Polygon", "MultiPolygon")
            or geom.is_empty
            or not geom.is_valid
        ):
            raise ValueError("Invalid or non-polygon truth geometry; no silent repair")
        geom = transform(geom, project, interleaved=False)
        if not geom.is_valid or not np.isfinite(geom.bounds).all():
            raise ValueError("Invalid projected truth geometry")
        projected.append(geom)
    return unary_union(projected)


def import_truth(
    perimeter: Path,
    coverage: Path,
    *,
    grid: Grid,
    source_crs: str,
    valid_sim_ms: int,
    published_sim_ms: int,
    source_id: str,
    source_url: str,
    out: Path,
) -> dict:
    if not source_id.strip() or not source_url.strip():
        raise ValueError("Independent source identifier and URL required")
    if (
        type(valid_sim_ms) is not int
        or type(published_sim_ms) is not int
        or valid_sim_ms < 0
        or published_sim_ms < valid_sim_ms
    ):
        raise ValueError(
            "Expected explicit valid time and publication time >= valid time"
        )
    if out.exists() or out.with_suffix(".provenance.json").exists():
        raise FileExistsError(out)
    burned_geometry = polygons(perimeter, source_crs, grid)
    valid_geometry = polygons(coverage, source_crs, grid)
    if not valid_geometry.covers(burned_geometry):
        raise ValueError("Coverage must contain the mapped burned geometry")
    kwargs = dict(
        out_shape=(grid.ny, grid.nx),
        transform=from_origin(grid.x0, grid.y0, grid.cell_m, grid.cell_m),
        fill=0,
        all_touched=False,
        dtype="uint8",
    )
    burned = rasterize([(burned_geometry, 1)], **kwargs)
    valid = rasterize([(valid_geometry, 1)], **kwargs).astype(bool)
    if not valid.any() or not burned.any():
        raise ValueError(
            "No covered/burned cell centers on evaluation grid; check CRS/domain"
        )
    audit = dict(
        source_id=source_id,
        source_url=source_url,
        valid_sim_ms=valid_sim_ms,
        published_sim_ms=published_sim_ms,
        source_crs=source_crs,
        grid=grid.model_dump(),
        grid_sha256=grid_digest(grid),
        perimeter_sha256=digest(perimeter),
        coverage_sha256=digest(coverage),
        rasterization="cell_center; all_touched=False",
        independence="requires_source_review",
        burned_cells=int(burned.sum()),
        valid_cells=int(valid.sum()),
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("xb") as f:
        np.savez_compressed(
            f,
            burned=burned,
            valid=valid,
            valid_sim_ms=np.int64(valid_sim_ms),
            grid_sha256=grid_digest(grid),
        )
    audit["truth_sha256"] = digest(out)
    out.with_suffix(".provenance.json").write_text(json.dumps(audit, indent=2) + "\n")
    return audit


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--perimeter", type=Path, required=True)
    p.add_argument("--coverage", type=Path, required=True)
    p.add_argument("--grid", type=Path, required=True, help="historical.Grid JSON")
    p.add_argument("--source-crs", required=True)
    p.add_argument("--valid-sim-ms", type=int, required=True)
    p.add_argument("--published-sim-ms", type=int, required=True)
    p.add_argument("--source-id", required=True)
    p.add_argument("--source-url", required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    print(
        json.dumps(
            import_truth(
                a.perimeter,
                a.coverage,
                grid=Grid.model_validate_json(a.grid.read_text()),
                source_crs=a.source_crs,
                valid_sim_ms=a.valid_sim_ms,
                published_sim_ms=a.published_sim_ms,
                source_id=a.source_id,
                source_url=a.source_url,
                out=a.out,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
