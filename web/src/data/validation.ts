import { QUANTILES } from '../contracts.ts'
import type { CycleBundle } from '../contracts.ts'
import type { ScenarioManifest, GeoCollection, IndexEntry } from './source.ts'
import { assetUrl } from './assetUrl.ts'

export function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new Error('Expected record')
  return value as Record<string, unknown>
}
export function integer(value: unknown): asserts value is number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value))
    throw new Error('Expected integer milliseconds/count')
}
function finite(value: unknown): asserts value is number {
  if (typeof value !== 'number' || !Number.isFinite(value))
    throw new Error('Expected finite number')
}
function text(value: unknown): asserts value is string {
  if (typeof value !== 'string' || !value)
    throw new Error('Expected nonempty string')
}
function probability(value: unknown) {
  finite(value)
  if (value < 0 || value > 1) throw new Error('Invalid probability')
}
function list(value: unknown): unknown[] {
  if (!Array.isArray(value) || value.length > 100_000)
    throw new Error('Expected bounded array')
  return value
}
function strings(value: unknown) {
  for (const item of list(value)) text(item)
}
function times(row: Record<string, unknown>) {
  for (const [key, value] of Object.entries(row))
    if (/(?:_sim_ms|_wall_ms)$/.test(key) && value !== null) integer(value)
}
function coordinate(value: unknown) {
  if (!Array.isArray(value) || value.length !== 2)
    throw new Error('Expected lon/lat pair')
  finite(value[0])
  finite(value[1])
  if (Math.abs(value[0]) > 180 || Math.abs(value[1]) > 90)
    throw new Error('Invalid longitude/latitude')
}
export function validateManifest(
  value: unknown,
  scenario: string,
  base: URL
): ScenarioManifest {
  const m = record(value)
  if (m.scenario_id !== scenario) throw new Error('Scenario mismatch')
  text(m.title)
  text(m.tz)
  try {
    new Intl.DateTimeFormat('en', { timeZone: m.tz })
  } catch {
    throw new Error('Invalid timezone')
  }
  if (
    typeof m.is_fixture !== 'boolean' ||
    !(m.config_hash === null || typeof m.config_hash === 'string')
  )
    throw new Error('Invalid provenance/config identity')
  const bbox = list(m.aoi_bbox)
  if (bbox.length !== 4) throw new Error('Invalid AOI bounds')
  coordinate([bbox[0], bbox[1]])
  coordinate([bbox[2], bbox[3]])
  if (
    (bbox[0] as number) >= (bbox[2] as number) ||
    (bbox[1] as number) >= (bbox[3] as number)
  )
    throw new Error('Invalid AOI bounds')
  const initial = record(m.initial_view)
  coordinate([initial.longitude, initial.latitude])
  for (const key of ['zoom', 'pitch', 'bearing']) finite(initial[key])
  if (
    (initial.zoom as number) < 0 ||
    (initial.pitch as number) < 0 ||
    (initial.pitch as number) > 85
  )
    throw new Error('Invalid initial map view')
  const honesty = record(m.honesty)
  strings(honesty.real)
  strings(honesty.simulated)
  for (const key of [
    't_ignition_sim_ms',
    'replay_start_sim_ms',
    'replay_end_sim_ms',
  ])
    integer(m[key])
  if ((m.replay_start_sim_ms as number) > (m.replay_end_sim_ms as number))
    throw new Error('Invalid replay bounds')
  integer(m.horizon_min)
  integer(m.step_min)
  if (
    m.horizon_min <= 0 ||
    m.horizon_min > 508 ||
    m.horizon_min % 2 ||
    m.step_min !== 30
  )
    throw new Error('Unsupported forecast horizon/lead step')
  if (JSON.stringify(m.quantiles) !== JSON.stringify(QUANTILES))
    throw new Error('Quantile contract mismatch')
  const grid = record(m.grid)
  integer(grid.nx)
  integer(grid.ny)
  finite(grid.cell_m)
  if (
    grid.nx < 1 ||
    grid.ny < 1 ||
    grid.nx * grid.ny > 2_000_000 ||
    grid.cell_m <= 0 ||
    grid.crs !== 'EPSG:32611'
  )
    throw new Error('Invalid grid')
  const corners = record(grid.corners)
  for (const key of ['tl', 'tr', 'br', 'bl']) coordinate(corners[key])
  const terrain = record(m.terrain)
  if (terrain.encoding !== 'terrarium')
    throw new Error('Unsupported terrain encoding')
  for (const key of ['elevation_url', 'texture_url'])
    assetUrl(base, terrain[key] as string)
  const statics = record(m.static)
  for (const key of [
    'zones_url',
    'assets_url',
    'sensors_url',
    'satellites_url',
    'timeline_url',
    'area_x_url',
    'spawn_points_url',
  ])
    assetUrl(base, statics[key] as string)
  if (statics.scans_dir !== null) assetUrl(base, statics.scans_dir as string)
  return m as unknown as ScenarioManifest
}
export function validateIndex(
  value: unknown,
  scenario: string,
  branch: string,
  base: URL
): IndexEntry[] {
  const index = record(value)
  if (index.scenario_id !== scenario || index.branch_id !== branch)
    throw new Error('Index identity mismatch')
  const entries = list(index.cycles)
    .map((item) => {
      const row = record(item)
      integer(row.knowledge_sim_ms)
      text(row.bundle)
      assetUrl(base, row.bundle)
      return {
        knowledge_sim_ms: row.knowledge_sim_ms,
        bundle: row.bundle,
        branch_id: branch,
      }
    })
    .sort((a, b) => a.knowledge_sim_ms - b.knowledge_sim_ms)
  if (
    new Set(entries.map((e) => e.knowledge_sim_ms)).size !== entries.length ||
    new Set(entries.map((e) => e.bundle)).size !== entries.length
  )
    throw new Error('Duplicate index entry')
  return entries
}
export function validateGeometryCollection(value: unknown): GeoCollection {
  const collection = record(value)
  if (collection.type !== 'FeatureCollection')
    throw new Error('Expected GeoJSON FeatureCollection')
  for (const item of list(collection.features)) {
    const feature = record(item),
      geometry = record(feature.geometry)
    if (feature.type !== 'Feature') throw new Error('Expected GeoJSON feature')
    record(feature.properties)
    const depths: Record<string, number> = {
      Point: 0,
      MultiPoint: 1,
      LineString: 1,
      MultiLineString: 2,
      Polygon: 2,
      MultiPolygon: 3,
    }
    if (typeof geometry.type !== 'string' || !(geometry.type in depths))
      throw new Error('Unsupported geometry')
    const visit = (coordinates: unknown, depth: number) => {
      if (!depth) coordinate(coordinates)
      else for (const child of list(coordinates)) visit(child, depth - 1)
    }
    visit(geometry.coordinates, depths[geometry.type])
  }
  return collection as unknown as GeoCollection
}
const requiredRows: Record<string, string> = {
  zone_risk:
    'cycle_id scenario_id branch_id zone_id knowledge_sim_ms p_by_lead eta_q10_sim_ms eta_q50_sim_ms eta_q90_sim_ms act_by_sim_ms act_by_basis evac_min population housing n_critical tier risk_score rank status trend drivers',
  observations:
    'obs_id scenario_id branch_id sensor_id kind observed_sim_ms available_sim_ms geometry hot fdc_class report_kind confidence frp_mw trust provenance status cycle_id influence',
  scan_events:
    'scan_id scenario_id branch_id sensor_id mode t_start_sim_ms t_end_sim_ms available_sim_ms n_pixels_aoi n_hot n_clear assimilated pixels_path provenance',
  alerts:
    'alert_id scenario_id branch_id zone_ids level rule_id rule_text cycle_id triggered_sim_ms act_by_sim_ms state text voice_script draft_source briefing_id decided_by decided_wall_ms call_id call_status acked_via acked_wall_ms acked_sim_ms',
  briefings:
    'briefing_id branch_id cycle_id alert_id model prompt_version headline bullets text voice_script grounded unmatched_tokens fallback_used latency_ms',
  sensors:
    'sensor_id kind name platform provenance enabled status last_data_sim_ms latency_s lon lat heading_deg hfov_deg pixel_m',
  people:
    'person_id kind label lon lat location_source zone_id status p_60 eta_q50_sim_ms act_by_sim_ms alerted_alert_id acknowledged last_seen_wall_ms provenance',
  reports: 'report_id status reject_reason observation_id',
  metrics: 'key branch_id label value unit definition computed_sim_ms detail',
  events:
    'event_id branch_id sim_ms wall_ms stage actor level message ref_kind ref_id duration_ms',
}
function requireFields(row: Record<string, unknown>, fields: string) {
  for (const key of fields.split(' '))
    if (!(key in row)) throw new Error(`Missing wire field ${key}`)
}
function observationGeometry(value: unknown) {
  const g = record(value)
  if (g.type === 'footprint') {
    const ring = list(g.ring)
    if (ring.length < 4) throw new Error('Invalid observation footprint')
    for (const point of ring) coordinate(point)
    if (JSON.stringify(ring[0]) !== JSON.stringify(ring.at(-1)))
      throw new Error('Unclosed observation footprint')
  } else if (g.type === 'point_radius' || g.type === 'bearing_ray') {
    coordinate([g.lon, g.lat])
    if (g.type === 'point_radius') {
      finite(g.radius_m)
      if (g.radius_m <= 0) throw new Error('Invalid report radius')
    } else {
      finite(g.bearing_deg)
      finite(g.sigma_deg)
      finite(g.max_range_m)
      if (g.sigma_deg <= 0 || g.max_range_m <= 0)
        throw new Error('Invalid bearing geometry')
    }
  } else throw new Error('Unknown observation geometry')
}
export function validateBundle(
  value: unknown,
  entry: IndexEntry,
  manifest: ScenarioManifest,
  base: URL
): CycleBundle {
  const b = record(value),
    issue = entry.knowledge_sim_ms
  if (
    b.schema_version !== 2 ||
    b.scenario_id !== manifest.scenario_id ||
    b.branch_id !== entry.branch_id ||
    b.knowledge_sim_ms !== issue
  )
    throw new Error('Bundle schema/identity/time mismatch')
  text(b.bundle_id)
  const seen = new Set<string>()
  const arrays = [
    'zone_risk',
    'observations',
    'scan_events',
    'alerts',
    'briefings',
    'sensors',
    'people',
    'reports',
    'metrics',
    'events',
  ]
  for (const key of arrays)
    for (const value of list(b[key])) {
      const row = record(value)
      requireFields(row, requiredRows[key])
      times(row)
      if ('scenario_id' in row && row.scenario_id !== b.scenario_id)
        throw new Error('Row scenario mismatch')
      if ('branch_id' in row && row.branch_id !== b.branch_id)
        throw new Error('Row branch mismatch')
      const idKey = (
        {
          zone_risk: 'zone_id',
          observations: 'obs_id',
          scan_events: 'scan_id',
          alerts: 'alert_id',
          briefings: 'briefing_id',
          sensors: 'sensor_id',
          people: 'person_id',
          reports: 'report_id',
          metrics: 'key',
          events: 'event_id',
        } as Record<string, string>
      )[key]
      text(row[idKey])
      const id = `${key}:${row[idKey]}`
      if (seen.has(id)) throw new Error('Duplicate row ID within bundle')
      seen.add(id)
      if ('available_sim_ms' in row) {
        integer(row.available_sim_ms)
        if (row.available_sim_ms > issue) throw new Error('Future evidence')
        if (
          'observed_sim_ms' in row &&
          (row.observed_sim_ms as number) > row.available_sim_ms
        )
          throw new Error('Observation before availability mismatch')
      }
      if (key === 'observations') {
        integer(row.observed_sim_ms)
        integer(row.available_sim_ms)
        probability(row.trust)
        probability(row.confidence)
        if (
          !['real', 'simulated', 'injected', 'derived'].includes(
            row.provenance as string
          )
        )
          throw new Error('Invalid observation provenance')
        if (
          ![
            'goes_pixel',
            'viirs_pixel',
            'camera_bearing',
            'ground_report',
            'thermal_footprint',
          ].includes(row.kind as string)
        )
          throw new Error('Invalid observation kind')
        observationGeometry(row.geometry)
        if (
          typeof row.hot !== 'boolean' ||
          !['pending', 'assimilated', 'rejected'].includes(row.status as string)
        )
          throw new Error('Invalid observation state')
      }
      if (key === 'zone_risk') {
        if (
          row.knowledge_sim_ms !== issue ||
          row.cycle_id !== record(b.cycle).cycle_id
        )
          throw new Error('Zone/cycle mismatch')
        const probabilities = list(row.p_by_lead)
        if (probabilities.length !== 12)
          throw new Error('Lead contract mismatch')
        let previous = 0
        for (const p of probabilities) {
          probability(p)
          if ((p as number) < previous)
            throw new Error('Decreasing arrival probability')
          previous = p as number
        }
        finite(row.risk_score)
        integer(row.rank)
      }
      if (key === 'alerts') {
        if (
          ![
            'proposed',
            'approved',
            'declined',
            'superseded',
            'calling',
            'called',
            'acknowledged',
            'failed',
          ].includes(row.state as string)
        )
          throw new Error('Invalid alert state')
        text(row.text)
        integer(row.triggered_sim_ms)
        if (row.triggered_sim_ms > issue) throw new Error('Future alert')
      }
      if (
        key === 'sensors' &&
        row.last_data_sim_ms !== null &&
        (row.last_data_sim_ms as number) > issue
      )
        throw new Error('Future sensor status')
      if (
        key === 'scan_events' &&
        (row.t_start_sim_ms as number) > (row.t_end_sim_ms as number)
      )
        throw new Error('Reversed scan times')
      if (key === 'events' && (row.sim_ms as number) > issue)
        throw new Error('Future activity event')
      if (
        key === 'scan_events' &&
        ((row.t_start_sim_ms as number) > issue ||
          (row.t_end_sim_ms as number) > issue)
      )
        throw new Error('Future scan')
      if (
        key === 'metrics' &&
        row.computed_sim_ms !== null &&
        (row.computed_sim_ms as number) > issue
      )
        throw new Error('Future metric')
    }
  if (b.cycle !== null) {
    const c = record(b.cycle)
    requireFields(
      c,
      'cycle_id scenario_id branch_id knowledge_sim_ms data_through_sim_ms source filter_mode n_members ess tempering_alpha resampled n_obs_by_kind horizon_min step_min assets danger burned_ha front_km growth_ha_per_h head_bearing_deg params_summary model_version config_hash duration_ms'
    )
    times(c)
    if (
      c.scenario_id !== b.scenario_id ||
      c.branch_id !== b.branch_id ||
      c.knowledge_sim_ms !== issue
    )
      throw new Error('Cycle identity mismatch')
    text(c.cycle_id)
    text(c.model_version)
    integer(c.data_through_sim_ms)
    integer(c.n_members)
    integer(c.horizon_min)
    if (
      c.data_through_sim_ms > issue ||
      c.horizon_min !== manifest.horizon_min ||
      c.step_min !== manifest.step_min ||
      c.n_members < 1 ||
      c.n_members > 512
    )
      throw new Error('Invalid cycle metadata')
    finite(c.ess)
    probability(c.tempering_alpha)
    if (
      c.ess <= 0 ||
      c.ess > c.n_members + 1e-6 ||
      !['pf', 'reinit', 'none'].includes(c.filter_mode as string)
    )
      throw new Error('Invalid ensemble diagnostics')
    for (const key of [
      'burned_ha',
      'front_km',
      'growth_ha_per_h',
      'duration_ms',
    ])
      finite(c[key])
    if (c.head_bearing_deg !== null) finite(c.head_bearing_deg)
    if (c.config_hash !== manifest.config_hash)
      throw new Error('Configuration identity mismatch')
    if (
      typeof c.resampled !== 'boolean' ||
      !['cache', 'live'].includes(c.source as string)
    )
      throw new Error('Invalid cycle state')
    for (const count of Object.values(record(c.n_obs_by_kind))) {
      integer(count)
      if (count < 0) throw new Error('Invalid observation count')
    }
    for (const pair of Object.values(record(c.params_summary))) {
      const values = list(pair)
      if (values.length !== 2) throw new Error('Invalid parameter summary')
      values.forEach(finite)
    }
    record(c.danger)
    const assets = record(c.assets)
    for (const key of ['arrival_q', 'perimeter', 'isochrones'])
      assetUrl(base, assets[key] as string)
  } else if ((b.zone_risk as unknown[]).length)
    throw new Error('Pre-ignition bundle cannot publish zone forecast')
  return b as unknown as CycleBundle
}
