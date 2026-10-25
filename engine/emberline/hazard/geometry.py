"""Grid conventions and cached observation footprints; no frontend coordinates leak."""

from __future__ import annotations
import hashlib
import json
import numpy as np
from scipy.ndimage import gaussian_filter
from pyproj import CRS, Transformer, Geod
from shapely import contains_xy
from shapely.geometry import Polygon


def centers(grid):
    east = grid.x0 + (np.arange(grid.W) + 0.5) * grid.cell_m
    north = grid.y0 - (np.arange(grid.H) + 0.5) * grid.cell_m
    return np.meshgrid(east, north)


def north_to_south(array, dtype="<f8"):
    return np.ascontiguousarray(np.flip(array, axis=-2), dtype=dtype)


def signed_distance(mask, cell_m):
    from scipy.ndimage import distance_transform_edt

    mask = np.asarray(mask, dtype=bool)
    if not mask.any() or mask.all():
        raise ValueError("resolved seed needs both burned and unburned cells")
    return (distance_transform_edt(~mask) - distance_transform_edt(mask)) * cell_m


class GeometryCache:
    def __init__(self, grid):
        crs = CRS.from_user_input(grid.crs)
        if not crs.is_projected or any(
            axis.unit_name.lower() not in {"metre", "meter"} for axis in crs.axis_info
        ):
            raise ValueError("HazardModel requires a projected metric CRS")
        self.grid = grid
        self.x, self.y = centers(grid)
        self.transform = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        inverse = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
        self.lon, self.lat = inverse.transform(self.x, self.y)
        self.geod = Geod(ellps="WGS84")
        axes = []
        for bearing in [90.0, 0.0]:
            lon, lat, _ = self.geod.fwd(
                self.lon,
                self.lat,
                np.full(self.x.shape, bearing),
                np.full(self.x.shape, 10.0),
            )
            east, north = self.transform.transform(lon, lat)
            vector = np.array([east - self.x, north - self.y])
            vector /= np.linalg.norm(vector, axis=0)
            axes.append(vector)
        self.true_east, self.true_north = axes
        if (
            not np.isfinite(axes).all()
            or np.max(np.abs(np.sum(self.true_east * self.true_north, axis=0))) > 1e-4
        ):
            raise ValueError(
                "a conformal metric grid is required for true-east/north wind conversion"
            )
        self.cache = {}

    def prepare(self, observation):
        geometry = observation.geometry.model_dump(mode="json")
        expected = {
            "goes_pixel": "footprint",
            "viirs_pixel": "footprint",
            "thermal_footprint": "footprint",
            "ground_report": "point_radius",
            "camera_bearing": "bearing_ray",
        }
        if geometry["type"] != expected.get(observation.kind):
            raise ValueError("observation kind and geometry type disagree")
        # The geometry hash prevents a reused footprint_key from aliasing changed geometry.
        key = hashlib.sha256(
            json.dumps([observation.kind, geometry], sort_keys=True).encode()
        ).hexdigest()
        if key in self.cache:
            return self.cache[key]
        g = observation.geometry
        if g.type == "footprint":
            points = np.asarray(g.ring, dtype=np.float64)
            if (
                points.ndim != 2
                or points.shape[1] != 2
                or len(points) < 4
                or not np.isfinite(points).all()
                or (np.abs(points[:, 0]) > 180).any()
                or (np.abs(points[:, 1]) > 90).any()
                or not np.array_equal(points[0], points[-1])
            ):
                raise ValueError("invalid footprint ring")
            x, y = self.transform.transform(points[:, 0], points[:, 1])
            polygon = Polygon(np.column_stack([x, y]))
            if polygon.is_empty or not polygon.is_valid or polygon.area <= 0:
                raise ValueError("invalid footprint polygon")
            mask = contains_xy(polygon, self.x, self.y)
            # Fractional cell intersection resolves sub-cell footprints without fabricating a center hit.
            if not mask.any():
                from shapely.geometry import box

                h = self.grid.cell_m / 2
                candidates = (
                    (self.x + h >= polygon.bounds[0])
                    & (self.x - h <= polygon.bounds[2])
                    & (self.y + h >= polygon.bounds[1])
                    & (self.y - h <= polygon.bounds[3])
                )
                fraction = np.zeros(mask.shape, dtype=np.float64)
                for r, c in np.argwhere(candidates):
                    cell = box(
                        self.x[r, c] - h,
                        self.y[r, c] - h,
                        self.x[r, c] + h,
                        self.y[r, c] + h,
                    )
                    fraction[r, c] = (
                        polygon.intersection(cell).area / self.grid.cell_m**2
                    )
                raw = fraction
            else:
                raw = mask.astype(np.float64)
            sigma = {
                "goes_pixel": 1000,
                "viirs_pixel": 200,
                "thermal_footprint": 30,
            }.get(observation.kind, 0)
            spatial = (
                gaussian_filter(raw, sigma / self.grid.cell_m, mode="constant", cval=0)
                if sigma
                else raw
            )
            result = {
                "spatial": spatial,
                "support": raw > 0,
                "in_domain": bool(raw.any()),
            }
        else:
            if (
                not np.isfinite([g.lon, g.lat]).all()
                or not -180 <= g.lon <= 180
                or not -90 <= g.lat <= 90
            ):
                raise ValueError("invalid observation coordinates")
            x, y = self.transform.transform(g.lon, g.lat)
            d2 = (self.x - x) ** 2 + (self.y - y) ** 2
            if g.type == "point_radius":
                if not np.isfinite(g.radius_m) or g.radius_m <= 0:
                    raise ValueError("report radius must be positive")
                support = d2 <= (2 * g.radius_m) ** 2
                spatial = np.where(support, np.exp(-d2 / (2 * g.radius_m**2)), 0.0)
            else:
                if (
                    not np.isfinite([g.bearing_deg, g.sigma_deg, g.max_range_m]).all()
                    or g.sigma_deg <= 0
                    or g.max_range_m <= 0
                ):
                    raise ValueError("invalid camera geometry")
                bearing, _, range_m = self.geod.inv(
                    np.full(self.x.shape, g.lon),
                    np.full(self.x.shape, g.lat),
                    self.lon,
                    self.lat,
                )
                bearing = np.asarray(bearing) % 360
                d2 = np.asarray(range_m) ** 2
                delta = (bearing - g.bearing_deg + 180) % 360 - 180
                support = (
                    (d2 > 0)
                    & (d2 < min(g.max_range_m, 20_000) ** 2)
                    & (np.abs(delta) <= 3 * g.sigma_deg)
                )
                spatial = np.where(
                    support, np.exp(-0.5 * (delta / g.sigma_deg) ** 2), 0
                )
            result = {
                "spatial": spatial,
                "support": support,
                "in_domain": bool(support.any()),
            }
        self.cache[key] = result
        return result
