import type { Cycle } from '../contracts.ts'
import type { MapAssets, ScenarioManifest } from './source.ts'
import { assetUrl, readResponse } from './assetUrl.ts'
import { validateGeometryCollection } from './validation.ts'

export class ArrivalGridCache {
  private cache = new Map<string, MapAssets>()
  private readonly fetcher: typeof fetch
  private readonly base: URL
  private readonly capacity: number
  constructor(fetcher: typeof fetch, base: URL, capacity = 4) {
    this.fetcher = fetcher
    this.base = base
    this.capacity = capacity
    if (!Number.isInteger(capacity) || capacity < 1 || capacity > 4)
      throw new Error('Grid cache capacity must be 1..4')
  }
  clear() {
    this.cache.clear()
  }
  get size() {
    return this.cache.size
  }
  async load(
    cycle: Cycle,
    manifest: ScenarioManifest,
    signal: AbortSignal
  ): Promise<MapAssets> {
    const key = JSON.stringify([
      cycle.scenario_id,
      cycle.branch_id,
      cycle.cycle_id,
      cycle.model_version,
      cycle.config_hash,
      cycle.assets,
      manifest.grid,
    ])
    const cached = this.cache.get(key)
    signal.throwIfAborted()
    if (cached) {
      this.cache.delete(key)
      this.cache.set(key, cached)
      return cached
    }
    const [binary, perimeter, isochrones] = await Promise.all([
      readResponse(
        this.fetcher,
        assetUrl(this.base, cycle.assets.arrival_q),
        signal,
        true
      ),
      readResponse(
        this.fetcher,
        assetUrl(this.base, cycle.assets.perimeter),
        signal
      ),
      readResponse(
        this.fetcher,
        assetUrl(this.base, cycle.assets.isochrones),
        signal
      ),
    ])
    signal.throwIfAborted()
    const arrival = binary as Uint8Array,
      cells = manifest.grid.nx * manifest.grid.ny
    if (arrival.byteLength !== 5 * cells)
      throw new Error('Arrival raster byte length mismatch')
    for (let cell = 0; cell < cells; cell++) {
      let previous = 0
      for (let q = 0; q < 5; q++) {
        const b = arrival[q * cells + cell]
        if (b < previous || (b !== 255 && b > cycle.horizon_min / 2))
          throw new Error('Invalid arrival quantile encoding/order')
        previous = b
      }
    }
    const p = validateGeometryCollection(perimeter),
      i = validateGeometryCollection(isochrones)
    if (JSON.stringify(p.features.map((f) => f.id)) !== '["burned","front"]')
      throw new Error('Perimeter feature IDs mismatch')
    const result = { arrival, perimeter: p, isochrones: i }
    this.cache.set(key, result)
    while (this.cache.size > this.capacity)
      this.cache.delete(this.cache.keys().next().value!)
    return result
  }
}
