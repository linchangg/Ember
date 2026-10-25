/// <reference lib="webworker" />
import { decodeForecast } from './forecastRaster.ts'
import type { RasterMode } from './forecastRaster.ts'
import { makeReprojection, reprojectPixels } from './reprojection.ts'
import type { RasterGrid, Reprojection } from './reprojection.ts'
const worker = self as unknown as DedicatedWorkerGlobalScope
let key = '',
  lookup: Reprojection | undefined
worker.onmessage = (
  event: MessageEvent<{
    id: number
    grid: RasterGrid
    bytes: Uint8Array
    issueMs: number
    viewMs: number
    horizonMin: number
    mode: RasterMode
  }>
) => {
  const request = event.data
  try {
    const next = JSON.stringify(request.grid)
    if (next !== key || !lookup) {
      lookup = makeReprojection(request.grid)
      key = next
    }
    const decoded = decodeForecast(
      request.bytes,
      request.grid.nx,
      request.grid.ny,
      request.issueMs,
      request.viewMs,
      request.horizonMin,
      request.mode
    )
    const rgba = reprojectPixels(decoded.rgba, lookup)
    worker.postMessage(
      {
        id: request.id,
        rgba,
        width: lookup.width,
        height: lookup.height,
        coordinates: lookup.coordinates,
        reached: decoded.reached,
        burned: decoded.burned,
      },
      [rgba.buffer]
    )
  } catch (error) {
    worker.postMessage({
      id: request.id,
      error: error instanceof Error ? error.message : String(error),
    })
  }
}
