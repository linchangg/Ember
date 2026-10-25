/** Exact CRS transforms with nearest-cell sampling; output image is Web Mercator. */
import proj4 from 'proj4'
import type { ScenarioManifest } from '../data/source.ts'
export type RasterGrid = ScenarioManifest['grid']
export type Coordinates = [
  [number, number],
  [number, number],
  [number, number],
  [number, number],
]
const UTM = '+proj=utm +zone=11 +datum=WGS84 +units=m +no_defs'
const geographic = proj4('EPSG:4326', UTM)
const mercator = proj4('EPSG:4326', 'EPSG:3857')
export interface Reprojection {
  width: number
  height: number
  indices: Int32Array
  coordinates: Coordinates
  origin: [number, number]
  bounds: [number, number, number, number]
  maxPixelGroundM: number
}
export function gridOrigin(grid: RasterGrid): [number, number] {
  if (
    grid.crs !== 'EPSG:32611' ||
    !Number.isSafeInteger(grid.nx) ||
    !Number.isSafeInteger(grid.ny) ||
    grid.nx < 1 ||
    grid.ny < 1 ||
    grid.nx * grid.ny > 2_000_000 ||
    !Number.isFinite(grid.cell_m) ||
    grid.cell_m <= 0
  )
    throw new Error('Unsupported raster grid')
  const origin = geographic.forward(grid.corners.tl) as [number, number]
  // Outer-corner coordinates may be rounded on the wire; tolerate at most 1 metre.
  for (const [key, dx, dy] of [
    ['tr', grid.nx * grid.cell_m, 0],
    ['bl', 0, -grid.ny * grid.cell_m],
    ['br', grid.nx * grid.cell_m, -grid.ny * grid.cell_m],
  ] as const) {
    const point = geographic.forward(grid.corners[key])
    if (Math.hypot(point[0] - origin[0] - dx, point[1] - origin[1] - dy) > 1)
      throw new Error('Grid corners are inconsistent with projected dimensions')
  }
  return origin
}
export function cellCenter(
  grid: RasterGrid,
  row: number,
  col: number
): [number, number] {
  const [x0, y0] = gridOrigin(grid)
  if (row < 0 || row >= grid.ny || col < 0 || col >= grid.nx)
    throw new Error('Cell outside grid')
  return geographic.inverse([
    x0 + (col + 0.5) * grid.cell_m,
    y0 - (row + 0.5) * grid.cell_m,
  ]) as [number, number]
}
export function makeReprojection(grid: RasterGrid): Reprojection {
  const origin = gridOrigin(grid),
    [x0, y0] = origin
  // Sample edges as well as corners to include curvature extrema in the bounding box.
  const points: number[][] = []
  for (let i = 0; i <= 32; i++) {
    const u = i / 32
    for (const [x, y] of [
      [x0 + u * grid.nx * grid.cell_m, y0],
      [x0 + u * grid.nx * grid.cell_m, y0 - grid.ny * grid.cell_m],
      [x0, y0 - u * grid.ny * grid.cell_m],
      [x0 + grid.nx * grid.cell_m, y0 - u * grid.ny * grid.cell_m],
    ]) {
      const ll = geographic.inverse([x, y])
      if (Math.abs(ll[1]) > 80)
        throw new Error('Grid outside supported map latitude')
      points.push(mercator.forward(ll))
    }
  }
  const west = Math.min(...points.map((p) => p[0])),
    east = Math.max(...points.map((p) => p[0]))
  const south = Math.min(...points.map((p) => p[1])),
    north = Math.max(...points.map((p) => p[1]))
  const width = Math.min(2048, Math.ceil(grid.nx * 2)),
    height = Math.min(2048, Math.ceil(grid.ny * 2))
  const maxPixelGroundM = Math.max(
    (east - west) / width,
    (north - south) / height
  )
  // Mercator metres overestimate ground lengths at this latitude: this bound is conservative.
  if (maxPixelGroundM * Math.SQRT2 > grid.cell_m)
    throw new Error(
      'Raster output resolution cannot meet one-cell placement gate'
    )
  const indices = new Int32Array(width * height)
  indices.fill(-1)
  const toGrid = proj4('EPSG:3857', UTM)
  for (let row = 0; row < height; row++)
    for (let col = 0; col < width; col++) {
      const [x, y] = toGrid.forward([
        west + ((col + 0.5) * (east - west)) / width,
        north - ((row + 0.5) * (north - south)) / height,
      ])
      const sourceCol = Math.floor((x - x0) / grid.cell_m),
        sourceRow = Math.floor((y0 - y) / grid.cell_m)
      if (
        sourceCol >= 0 &&
        sourceCol < grid.nx &&
        sourceRow >= 0 &&
        sourceRow < grid.ny
      )
        indices[row * width + col] = sourceRow * grid.nx + sourceCol
    }
  const coordinates = [
    [west, north],
    [east, north],
    [east, south],
    [west, south],
  ].map((p) => mercator.inverse(p)) as Coordinates
  return {
    width,
    height,
    indices,
    coordinates,
    origin,
    bounds: [west, south, east, north],
    maxPixelGroundM,
  }
}
export function reprojectPixels(rgba: Uint8ClampedArray, lookup: Reprojection) {
  const result = new Uint8ClampedArray(lookup.indices.length * 4)
  for (let i = 0; i < lookup.indices.length; i++) {
    const cell = lookup.indices[i]
    if (cell >= 0) {
      if (cell * 4 + 3 >= rgba.length)
        throw new Error('Reprojection index outside source')
      result.set(rgba.subarray(cell * 4, cell * 4 + 4), i * 4)
    }
  }
  return result
}
