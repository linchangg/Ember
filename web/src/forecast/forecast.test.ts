import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import proj4 from 'proj4'
import { decodeForecast, BURNED_COLOR } from './forecastRaster.ts'
import {
  cellCenter,
  gridOrigin,
  makeReprojection,
  reprojectPixels,
} from './reprojection.ts'
import type { RasterGrid } from './reprojection.ts'
const golden = JSON.parse(
  await readFile(
    new URL('./georeference.reference.json', import.meta.url),
    'utf8'
  )
)
const root =
  process.env.EMBERLINE_FIXTURE_DIR ??
  fileURLToPath(new URL('../../../fixtures/', import.meta.url))
const scenario = process.env.EMBERLINE_SCENARIO_ID ?? 'fixture'
const manifest = JSON.parse(
  await readFile(path.join(root, `static/${scenario}/manifest.json`), 'utf8')
)
const index = JSON.parse(
  await readFile(path.join(root, `cache/${scenario}/main/index.json`), 'utf8')
)
const distance = (a: number[], b: number[]) => {
  const radians = Math.PI / 180,
    lat = (a[1] + b[1]) * 0.5 * radians
  return (
    Math.hypot(
      (a[0] - b[0]) * radians * Math.cos(lat),
      (a[1] - b[1]) * radians
    ) * 6378137
  )
}
test('all five arrival quantiles and 30/120/360 minute masks match independent raw-byte counting', async () => {
  const { nx, ny } = manifest.grid,
    cells = nx * ny
  let forecasts = 0
  for (const entry of index.cycles) {
    const bundle = JSON.parse(
      await readFile(path.join(root, entry.bundle), 'utf8')
    )
    if (!bundle.cycle) continue
    forecasts++
    const bytes = new Uint8Array(
      await readFile(path.join(root, bundle.cycle.assets.arrival_q))
    )
    for (const lead of [30, 120, 360]) {
      const issue = bundle.cycle.knowledge_sim_ms
      const result = decodeForecast(
        bytes,
        nx,
        ny,
        issue,
        issue + lead * 60000,
        360
      )
      for (let cell = 0; cell < cells; cell++) {
        let reached = 0
        for (let q = 0; q < 5; q++)
          if (
            bytes[q * cells + cell] !== 255 &&
            2 * bytes[q * cells + cell] <= lead
          )
            reached++
        assert.equal(result.classes[cell], reached)
      }
      for (const q of [0, 1, 2, 3, 4] as const) {
        const result = decodeForecast(
          bytes,
          nx,
          ny,
          issue,
          issue + lead * 60000,
          360,
          q
        )
        let count = 0
        for (let cell = 0; cell < cells; cell++)
          if (
            bytes[q * cells + cell] !== 255 &&
            2 * bytes[q * cells + cell] <= lead
          )
            count++
        assert.equal(result.reached, count)
      }
    }
  }
  assert.equal(forecasts, 36)
})
test('unreached cells stay transparent, certain burned cells use burned styling, and two-minute thresholds are exact', () => {
  const bytes = new Uint8Array([
    255, 0, 1, 255, 0, 1, 255, 0, 1, 255, 0, 1, 255, 0, 1,
  ])
  const early = decodeForecast(bytes, 3, 1, 0, 119999, 360),
    onTime = decodeForecast(bytes, 3, 1, 0, 120000, 360)
  assert.equal(early.rgba[3], 0)
  assert.equal(early.classes[0], 0)
  assert.deepEqual(Array.from(early.rgba.slice(4, 8)), Array.from(BURNED_COLOR))
  assert.equal(early.rgba[11], 0)
  assert(onTime.rgba[11] > 0)
  assert.equal(onTime.classes[2], 5)
})
test('invalid lengths, unordered quantiles, unsupported times and beyond-horizon selections fail', () => {
  assert.throws(() => decodeForecast(new Uint8Array(4), 1, 1, 0, 0, 360))
  assert.throws(() =>
    decodeForecast(new Uint8Array([5, 4, 3, 2, 1]), 1, 1, 0, 0, 360)
  )
  assert.throws(() => decodeForecast(new Uint8Array(5), 1, 1, 1, 0, 360))
  assert.throws(() =>
    decodeForecast(new Uint8Array(5), 1, 1, 0, 361 * 60000, 360)
  )
})
test('JavaScript UTM cell centers agree with independent PROJ coordinates within 10 cm', () => {
  for (const c of golden.cases)
    for (const p of c.samples)
      assert(distance(cellCenter(c.grid, p.row, p.col), [p.lon, p.lat]) < 0.1)
})
test('Mercator output samples remain within one source cell; outside the UTM domain stays transparent', () => {
  const grid = golden.cases[0].grid as RasterGrid,
    lookup = makeReprojection(grid)
  assert(lookup.maxPixelGroundM * Math.SQRT2 < grid.cell_m)
  const toGrid = proj4(
    'EPSG:3857',
    '+proj=utm +zone=11 +datum=WGS84 +units=m +no_defs'
  )
  const [west, south, east, north] = lookup.bounds,
    [x0, y0] = lookup.origin
  for (let i = 0; i < lookup.indices.length; i += 31) {
    const cell = lookup.indices[i]
    if (cell < 0) continue
    const row = Math.floor(i / lookup.width),
      col = i % lookup.width
    const [x, y] = toGrid.forward([
      west + ((col + 0.5) * (east - west)) / lookup.width,
      north - ((row + 0.5) * (north - south)) / lookup.height,
    ])
    const centerX = x0 + ((cell % grid.nx) + 0.5) * grid.cell_m,
      centerY = y0 - (Math.floor(cell / grid.nx) + 0.5) * grid.cell_m
    assert(
      Math.hypot(centerX - x, centerY - y) <= grid.cell_m / Math.SQRT2 + 1e-5
    )
  }
  const image = reprojectPixels(
    new Uint8ClampedArray(grid.nx * grid.ny * 4).fill(255),
    lookup
  )
  for (let i = 0; i < lookup.indices.length; i++)
    if (lookup.indices[i] < 0) assert.equal(image[4 * i + 3], 0)
})
test('northwest/southeast markers retain north-first orientation through reprojection', () => {
  const template = golden.cases[0].grid as RasterGrid,
    [x0, y0] = gridOrigin(template)
  const toGeo = proj4(
    '+proj=utm +zone=11 +datum=WGS84 +units=m +no_defs',
    'EPSG:4326'
  )
  const grid = {
    ...template,
    nx: 4,
    ny: 4,
    cell_m: 90,
    corners: {
      tl: toGeo.forward([x0, y0]),
      tr: toGeo.forward([x0 + 360, y0]),
      br: toGeo.forward([x0 + 360, y0 - 360]),
      bl: toGeo.forward([x0, y0 - 360]),
    },
  } as RasterGrid
  const bytes = new Uint8Array(5 * 16).fill(255)
  for (let q = 0; q < 5; q++) {
    bytes[q * 16] = 0
    bytes[q * 16 + 15] = 60
  }
  const decoded = decodeForecast(bytes, 4, 4, 0, 120 * 60000, 360),
    lookup = makeReprojection(grid),
    pixels = reprojectPixels(decoded.rgba, lookup)
  const positions = (cell: number) =>
    Array.from(lookup.indices.entries())
      .filter(([, value]) => value === cell)
      .map(([i]) => [Math.floor(i / lookup.width), i % lookup.width])
  const nw = positions(0),
    se = positions(15)
  assert(nw.length && se.length)
  assert(Math.max(...nw.map((p) => p[0])) < Math.min(...se.map((p) => p[0])))
  assert(Math.max(...nw.map((p) => p[1])) < Math.min(...se.map((p) => p[1])))
  for (let i = 0; i < lookup.indices.length; i++)
    if (lookup.indices[i] === 0)
      assert.deepEqual(
        Array.from(pixels.slice(i * 4, i * 4 + 4)),
        Array.from(BURNED_COLOR)
      )
})
test('inconsistent projected corners are rejected instead of stretching a forecast', () => {
  const grid = structuredClone(golden.cases[0].grid)
  grid.corners.tr[0] += 0.01
  assert.throws(() => makeReprojection(grid), /corners/)
})

test('map framing uses the actual published vector extent and handles empty geometry', async () => {
  const { vectorBounds } = await import('./mapAdapter.ts')
  assert.equal(vectorBounds({ type: 'FeatureCollection', features: [] }), null)
  assert.deepEqual(
    vectorBounds({
      type: 'FeatureCollection',
      features: [
        {
          type: 'Feature',
          properties: {},
          geometry: {
            type: 'MultiLineString',
            coordinates: [
              [
                [-118.2, 34.1],
                [-118.1, 34.3],
              ],
            ],
          },
        },
      ],
    }),
    [
      [-118.2, 34.1],
      [-118.1, 34.3],
    ]
  )
})
