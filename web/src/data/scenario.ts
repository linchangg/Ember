/** Linda's scenario interface: validated files; her replayClock remains the only clock. */
import type { CycleBundle } from '../contracts.ts'
import type {
  GeoCollection,
  IndexEntry,
  MapAssets,
  ScenarioManifest,
} from './source.ts'
import { assetRoot, assetUrl, readResponse, safeId } from './assetUrl.ts'
import {
  validateManifest,
  validateIndex,
  validateBundle,
  validateGeometryCollection,
} from './validation.ts'
import { ArrivalGridCache } from './arrivalGrid.ts'
import { projectGrid } from '../forecast/reprojection.ts'
import { decodeForecast } from '../forecast/forecastRaster.ts'
import type { RasterMode } from '../forecast/forecastRaster.ts'

export interface ScenarioEntry {
  bundle: CycleBundle
  arrival: Uint8Array | null
  burned: GeoCollection['features'][number]['geometry'] | null
  front: GeoCollection['features'][number]['geometry'] | null
  isochrones: GeoCollection['features']
  zoneRisk: Map<string, CycleBundle['zone_risk'][number]>
}
export interface Selection {
  status:
    | 'loading'
    | 'ready'
    | 'not-issued'
    | 'beyond-horizon'
    | 'error'
    | 'disposed'
  error: string | null
  issue: number | null
  entry: ScenarioEntry | null
}
export async function openScenario(
  scenario: string,
  options: {
    base?: string
    pageUrl?: string
    fetcher?: typeof fetch
    signal?: AbortSignal
  } = {}
) {
  safeId(scenario)
  const base = assetRoot(options.base ?? '/', options.pageUrl)
  const fetcher = options.fetcher ?? fetch
  const startup = new AbortController()
  const abort = () => startup.abort()
  options.signal?.addEventListener('abort', abort, { once: true })
  if (options.signal?.aborted) startup.abort()
  const json = (relative: string, signal = startup.signal) =>
    readResponse(fetcher, assetUrl(base, relative), signal)
  try {
    const manifest = validateManifest(
      await json(`static/${scenario}/manifest.json`),
      scenario,
      base
    )
    const [zones, assets, timeline, areaX, rawIndex] = await Promise.all([
      json(manifest.static.zones_url),
      json(manifest.static.assets_url),
      json(manifest.static.timeline_url),
      json(manifest.static.area_x_url),
      json(`cache/${scenario}/main/index.json`),
    ])
    const cycles = validateIndex(rawIndex, scenario, 'main', base)
    const checkedZones = validateGeometryCollection(zones)
    const checkedAssets = validateGeometryCollection(assets)
    const checkedArea = validateGeometryCollection({
      type: 'FeatureCollection',
      features: [areaX],
    }).features[0]
    if (checkedArea.geometry.type !== 'Polygon')
      throw new Error('Area X must be a polygon feature')
    if (
      !Array.isArray(timeline) ||
      timeline.some(
        (e) => !Number.isSafeInteger(e.t_sim_ms) || typeof e.label !== 'string'
      )
    )
      throw new Error('Invalid scenario timeline')
    const coordinates = projectGrid(manifest.grid)
    startup.signal.throwIfAborted()
    return new ScenarioFiles(
      manifest,
      checkedZones,
      checkedAssets,
      timeline,
      checkedArea,
      cycles,
      coordinates,
      base,
      fetcher
    )
  } finally {
    options.signal?.removeEventListener('abort', abort)
  }
}
class ScenarioFiles {
  readonly quantiles = [0.1, 0.3, 0.5, 0.7, 0.9]
  readonly cellLonLat: Float64Array
  readonly cornerLonLat: Float64Array
  private cache = new Map<number, ScenarioEntry>()
  private grids: ArrivalGridCache
  private controller: AbortController | null = null
  private generation = 0
  private context = ''
  private requestKey = ''
  private target = ''
  private disposed = false
  private state: Selection = {
    status: 'not-issued',
    error: null,
    issue: null,
    entry: null,
  }
  private mask: {
    entry: ScenarioEntry
    bin: number
    mode: RasterMode
    value: ReturnType<typeof decodeForecast>
  } | null = null
  readonly manifest: ScenarioManifest
  readonly zones: GeoCollection
  readonly assets: GeoCollection
  readonly timeline: { t_sim_ms: number; label: string }[]
  readonly areaX: unknown
  readonly cycles: IndexEntry[]
  private base: URL
  private fetcher: typeof fetch
  constructor(
    manifest: ScenarioManifest,
    zones: GeoCollection,
    assets: GeoCollection,
    timeline: { t_sim_ms: number; label: string }[],
    areaX: unknown,
    cycles: IndexEntry[],
    coordinates: ReturnType<typeof projectGrid>,
    base: URL,
    fetcher: typeof fetch
  ) {
    this.manifest = manifest
    this.zones = zones
    this.assets = assets
    this.timeline = timeline
    this.areaX = areaX
    this.cycles = cycles
    this.base = base
    this.fetcher = fetcher
    this.cellLonLat = coordinates.centers
    this.cornerLonLat = coordinates.corners
    this.grids = new ArrivalGridCache(fetcher, base)
  }
  getSnapshot = () => this.state
  cycleAt(time: number) {
    return (
      this.cycles.findLast((c) => c.knowledge_sim_ms <= time)
        ?.knowledge_sim_ms ?? null
    )
  }
  /** Nonblocking selection called by Linda's clock/UI loop; never returns an older issue. */
  select(now: number, view = now, branch = 'main', epoch = 0): Selection {
    if (this.disposed) return this.state
    if (!Number.isFinite(now) || !Number.isFinite(view))
      throw new Error('Invalid scenario selection time')
    const context = JSON.stringify([branch, epoch])
    if (context !== this.context) {
      this.context = context
      this.cache.clear()
      this.grids.clear()
      this.requestKey = ''
      this.mask = null
      this.controller?.abort()
      this.generation++
      this.target = ''
    }
    if (branch !== 'main') {
      this.state = {
        status: 'error',
        error:
          'This file replay has no configured branch lineage; hub branch integration is pending.',
        issue: null,
        entry: null,
      }
      return this.state
    }
    const issue = this.cycleAt(Math.min(now, view))
    if (issue === null) {
      this.controller?.abort()
      this.generation++
      this.requestKey = ''
      return (this.state = {
        status: 'not-issued',
        error: null,
        issue,
        entry: null,
      })
    }
    const target = JSON.stringify([context, issue])
    if (target !== this.target) {
      this.target = target
      this.controller?.abort()
      this.generation++
      this.requestKey = ''
    }
    const cached = this.cache.get(issue)
    if (cached) {
      this.cache.delete(issue)
      this.cache.set(issue, cached)
      const cycle = cached.bundle.cycle
      const status = !cycle
        ? 'not-issued'
        : view > issue + cycle.horizon_min * 60000
          ? 'beyond-horizon'
          : 'ready'
      return (this.state = {
        status,
        error: null,
        issue,
        entry: status === 'ready' ? cached : null,
      })
    }
    const key = JSON.stringify([context, issue])
    if (key !== this.requestKey) {
      this.requestKey = key
      this.controller?.abort()
      const controller = new AbortController()
      this.controller = controller
      const generation = ++this.generation
      this.state = { status: 'loading', error: null, issue, entry: null }
      void this.load(issue, controller.signal)
        .then((entry) => {
          if (this.disposed || generation !== this.generation) return
          this.cache.set(issue, entry)
          while (this.cache.size > 4)
            this.cache.delete(this.cache.keys().next().value!)
          // The next clock tick applies current view/horizon, rather than captured old view time.
          this.requestKey = ''
        })
        .catch((error) => {
          if (this.disposed || generation !== this.generation) return
          this.state = {
            status: 'error',
            error: error instanceof Error ? error.message : String(error),
            issue,
            entry: null,
          }
        })
    }
    return this.state
  }
  retry() {
    this.requestKey = ''
  }
  private async load(
    issue: number,
    signal: AbortSignal
  ): Promise<ScenarioEntry> {
    const index = this.cycles.find((c) => c.knowledge_sim_ms === issue)!
    const raw = await readResponse(
      this.fetcher,
      assetUrl(this.base, index.bundle),
      signal
    )
    const bundle = validateBundle(raw, index, this.manifest, this.base)
    const data: MapAssets | null = bundle.cycle
      ? await this.grids.load(bundle.cycle, this.manifest, signal)
      : null
    signal.throwIfAborted()
    this.grids.clear() // Entries own the bounded four-grid cache; avoid a second retained set.
    return {
      bundle,
      arrival: data?.arrival ?? null,
      burned:
        data?.perimeter.features.find((f) => f.id === 'burned')?.geometry ??
        null,
      front:
        data?.perimeter.features.find((f) => f.id === 'front')?.geometry ??
        null,
      isochrones: data?.isochrones.features ?? [],
      zoneRisk: new Map(bundle.zone_risk.map((z) => [z.zone_id, z])),
    }
  }
  raster(entry: ScenarioEntry, view: number, mode: RasterMode = 'probability') {
    const cycle = entry.bundle.cycle
    if (!cycle || !entry.arrival) return null
    const bin = Math.floor((view - cycle.knowledge_sim_ms) / 120000)
    if (
      this.mask?.entry === entry &&
      this.mask.bin === bin &&
      this.mask.mode === mode
    )
      return this.mask.value
    const value = decodeForecast(
      entry.arrival,
      this.manifest.grid.nx,
      this.manifest.grid.ny,
      cycle.knowledge_sim_ms,
      view,
      cycle.horizon_min,
      mode
    )
    this.mask = { entry, bin, mode, value }
    return value
  }
  dispose() {
    this.disposed = true
    this.generation++
    this.controller?.abort()
    this.cache.clear()
    this.grids.clear()
    this.mask = null
    this.state = { status: 'disposed', error: null, issue: null, entry: null }
  }
}
