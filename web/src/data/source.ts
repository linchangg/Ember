/** Internal dashboard boundary. Frozen wire types continue to come from contracts.ts. */
import type {
  Alert,
  Branch,
  Briefing,
  ClockState,
  Cycle,
  CycleBundle,
  Metric,
  Observation,
  Person,
  ScanEvent,
  SensorInfo,
  ZoneFeature,
  ZoneRisk,
} from '../contracts.ts'

export interface ScenarioManifest {
  scenario_id: string
  title: string
  tz: string
  config_hash: string | null
  is_fixture: boolean
  honesty: { real: string[]; simulated: string[]; derived?: string[] }
  aoi_bbox: [number, number, number, number]
  initial_view: {
    longitude: number
    latitude: number
    zoom: number
    pitch: number
    bearing: number
  }
  t_ignition_sim_ms: number
  replay_start_sim_ms: number
  replay_end_sim_ms: number
  horizon_min: number
  step_min: number
  quantiles: number[]
  grid: {
    nx: number
    ny: number
    cell_m: number
    crs: 'EPSG:32611'
    corners: Record<'tl' | 'tr' | 'br' | 'bl', [number, number]>
  }
  terrain: {
    elevation_url: string
    encoding: 'terrarium'
    bounds: number[]
    texture_url: string
    texture_date: string
    attribution: string[]
  }
  static: {
    zones_url: string
    assets_url: string
    sensors_url: string
    satellites_url: string
    timeline_url: string
    area_x_url: string
    spawn_points_url: string
    scans_dir: string | null
  }
  lake_ave: number[][]
}
export interface GeoCollection {
  type: 'FeatureCollection'
  features: {
    type: 'Feature'
    id?: string | number
    properties: Record<string, unknown>
    geometry: { type: string; coordinates: unknown }
  }[]
}
export interface IndexEntry {
  knowledge_sim_ms: number
  bundle: string
  branch_id: string
}
export interface MapAssets {
  arrival: Uint8Array
  perimeter: GeoCollection
  isochrones: GeoCollection
}
export type BundleReport = CycleBundle['reports'][number]
export interface Collections {
  cycles: Cycle[]
  zoneRisk: ZoneRisk[]
  observations: Observation[]
  scans: ScanEvent[]
  alerts: Alert[]
  briefings: Briefing[]
  sensors: SensorInfo[]
  people: Person[]
  reports: BundleReport[]
  metrics: Metric[]
  events: CycleBundle['events']
}
export interface Snapshot extends Collections {
  status: 'idle' | 'loading' | 'ready' | 'error' | 'disposed'
  error: string | null
  manifest: ScenarioManifest | null
  clock: ClockState | null
  view: { mode: 'live' | 'scrub'; viewTime: number }
  simTime: number
  pending: { simTime: number; viewTime: number } | null
  forecastStatus: 'not-issued' | 'available' | 'beyond-horizon'
  bundle: CycleBundle | null
  cycle: Cycle | null
  mapAssets: MapAssets | null
  branches: Branch[]
  zones: ZoneFeature[]
  evacOverrides: Readonly<Record<string, number>>
}
export interface Actions {
  play(): Promise<void>
  pause(): Promise<void>
  seek(time: number): Promise<void>
  setScrub(time: number): Promise<void>
  goLive(): Promise<void>
  setSpeed(speed: number): Promise<void>
  reset(): Promise<void>
  switchBranch(id: string): Promise<void>
  setPauseOnProposal(on: boolean): Promise<void>
  approve(id: string, text?: string): Promise<void>
  decline(id: string, reason: string): Promise<void>
  setEvacMin(zone: string, minutes: number): Promise<void>
  injectTapReport(lon: number, lat: number, kind: string): Promise<void>
}
export interface BranchComparison {
  cycle: Cycle
  mapAssets: MapAssets
  zoneRisk: ZoneRisk[]
}
export interface DataSource {
  compareBranch?(
    branch: string,
    issue: number,
    view: number,
    signal: AbortSignal
  ): Promise<BranchComparison | null>
  readonly actions: Actions
  getSnapshot(): Snapshot
  subscribe(listener: () => void): () => void
  initialize(): Promise<void>
  tick(): Promise<void>
  dispose(): void
}
export const emptyCollections = (): Collections => ({
  cycles: [],
  zoneRisk: [],
  observations: [],
  scans: [],
  alerts: [],
  briefings: [],
  sensors: [],
  people: [],
  reports: [],
  metrics: [],
  events: [],
})
export function initialSnapshot(): Snapshot {
  return {
    ...emptyCollections(),
    status: 'idle',
    error: null,
    manifest: null,
    clock: null,
    view: { mode: 'live', viewTime: 0 },
    simTime: 0,
    pending: null,
    forecastStatus: 'not-issued',
    bundle: null,
    cycle: null,
    mapAssets: null,
    branches: [],
    zones: [],
    evacOverrides: {},
  }
}
