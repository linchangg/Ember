/** SDK row normalization and frozen-contract lineage. No SDK types cross this boundary. */
import type { Branch, ClockState, Cycle, CycleBundle } from '../contracts.ts'
import { simNow } from '../contracts.ts'
import type { ScenarioManifest } from './source.ts'
import { validateBundle } from './validation.ts'
export type Row = Record<string, unknown>
export type HubRows = Record<string, Row[]>
export const HUB_TABLES = [
  'scenario',
  'zone',
  'clock',
  'branch',
  'cycle',
  'cycleHead',
  'zoneRisk',
  'observation',
  'scanEvent',
  'alert',
  'briefing',
  'sensor',
  'person',
  'report',
  'metric',
  'activityEvent',
] as const
export function adapt(row: Row): Row {
  const out: Row = {}
  for (const [name, value] of Object.entries(row)) {
    let key = name.replace(/[A-Z]/g, (c) => '_' + c.toLowerCase())
    const json = key.endsWith('_json')
    if (json) key = key.slice(0, -5)
    if (json && typeof value !== 'string')
      throw new Error(`Invalid JSON column ${name}`)
    out[key] = json
      ? JSON.parse(value as string)
      : value === undefined
        ? null
        : typeof value === 'bigint'
          ? Number(value)
          : value && typeof value === 'object' && 'toHexString' in value
            ? (value as { toHexString(): string }).toHexString()
            : value
  }
  return out
}
export function lineage(branches: Branch[], active: string, at: number) {
  const limits = new Map<string, number>()
  let id: string | null = active,
    limit = at
  while (id !== null) {
    if (limits.has(id)) throw new Error('Branch lineage cycle')
    const b = branches.find((b) => b.branch_id === id)
    if (!b) throw new Error('Missing branch ancestor')
    limits.set(id, limit)
    if (b.parent_id !== null) {
      if (!Number.isSafeInteger(b.fork_sim_ms))
        throw new Error('Missing branch fork time')
      limit = Math.min(limit, b.fork_sim_ms!)
    }
    id = b.parent_id
  }
  return limits
}
export function hubClock(rows: HubRows, scenario: string): ClockState {
  const c = rows.clock?.find((r) => r.scenario_id === scenario)
  if (!c || !['ok', 'computing', 'catching_up'].includes(String(c.status)))
    throw new Error('Hub clock unavailable or invalid')
  for (const name of [
    'anchor_sim_ms',
    'anchor_wall_ms',
    'min_sim_ms',
    'max_sim_ms',
    'epoch',
  ])
    if (!Number.isSafeInteger(c[name]))
      throw new Error('Invalid hub clock time')
  if (
    typeof c.playing !== 'boolean' ||
    typeof c.speed !== 'number' ||
    !Number.isFinite(c.speed) ||
    c.speed <= 0 ||
    Number(c.min_sim_ms) > Number(c.max_sim_ms)
  )
    throw new Error('Invalid hub clock bounds')
  return c as unknown as ClockState
}
export function selectHub(
  rows: HubRows,
  manifest: ScenarioManifest,
  base: URL,
  wall: number,
  scrub: number | null
) {
  const clock = hubClock(rows, manifest.scenario_id),
    now = Math.floor(simNow(clock, wall)),
    view = scrub ?? now,
    at = Math.min(now, view)
  const branches = (rows.branch ?? []) as unknown as Branch[]
  const limits = lineage(branches, clock.active_branch_id, at)
  const visible = (r: Row, time: string) =>
    limits.has(String(r.branch_id)) &&
    (r[time] === null || Number(r[time]) <= limits.get(String(r.branch_id))!)
  const heads = rows.cycleHead ?? []
  for (const head of heads)
    if (
      !(rows.cycle ?? []).some(
        (c) =>
          c.cycle_id === head.cycle_id &&
          c.knowledge_sim_ms === head.knowledge_sim_ms &&
          c.branch_id === head.branch_id
      )
    )
      throw new Error('Committed head references a missing cycle')
  const cycles = (rows.cycle ?? []).filter(
    (r) =>
      visible(r, 'knowledge_sim_ms') &&
      heads.some(
        (h) =>
          h.branch_id === r.branch_id &&
          Number(h.knowledge_sim_ms) >= Number(r.knowledge_sim_ms)
      )
  ) as unknown as Cycle[]
  cycles.sort(
    (a, b) =>
      a.knowledge_sim_ms - b.knowledge_sim_ms ||
      [...limits.keys()].indexOf(b.branch_id) -
        [...limits.keys()].indexOf(a.branch_id)
  )
  const cycle = cycles.at(-1) ?? null
  // Validate each branch separately: frozen bundles cannot contain rows from another branch.
  const combined: CycleBundle[] = []
  for (const [branch, limit] of limits) {
    const eligible = (table: string, time: string) =>
      (rows[table] ?? []).filter(
        (r) =>
          r.branch_id === branch &&
          (r[time] === null || Number(r[time]) <= limit)
      )
    const selected = cycle?.branch_id === branch ? cycle : null
    const b = {
      schema_version: 2,
      bundle_id: `hub_${branch}_${at}`,
      scenario_id: manifest.scenario_id,
      branch_id: branch,
      knowledge_sim_ms: at,
      cycle: selected,
      zone_risk: selected
        ? (rows.zoneRisk ?? []).filter((r) => r.cycle_id === selected.cycle_id)
        : [],
      observations: eligible('observation', 'available_sim_ms').filter(
        (r) =>
          r.cycle_id === null ||
          (rows.cycle ?? []).some(
            (c) => c.cycle_id === r.cycle_id && visible(c, 'knowledge_sim_ms')
          )
      ),
      scan_events: eligible('scanEvent', 'available_sim_ms').filter(
        (r) => Number(r.t_end_sim_ms) <= limit
      ),
      // Parent alert state has no transition timestamp. Omit inherited operational state;
      // it remains inspectable by the owning branch, never infer pre-fork approval state.
      alerts:
        branch === clock.active_branch_id
          ? eligible('alert', 'triggered_sim_ms')
          : [],
      briefings:
        branch === clock.active_branch_id
          ? (rows.briefing ?? [])
              .filter((r) => r.branch_id === branch)
              .filter((r) =>
                (rows.alert ?? []).some(
                  (a) =>
                    a.alert_id === r.alert_id && visible(a, 'triggered_sim_ms')
                )
              )
          : [],
      sensors: (rows.sensor ?? []).filter(
        (r) => r.last_data_sim_ms === null || Number(r.last_data_sim_ms) <= at
      ),
      people:
        branch === clock.active_branch_id && view >= now
          ? (rows.person ?? [])
          : [],
      reports: eligible('report', 'observed_sim_ms').filter((r) =>
        ['accepted', 'rejected'].includes(String(r.status))
      ),
      metrics: eligible('metric', 'computed_sim_ms'),
      events: eligible('activityEvent', 'sim_ms'),
    }
    // Bundle cycle identity is its issue, while the hub collections have their own causal cutoffs.
    // Validate the forecast/risk at issue; validate operational collections separately at now/view.
    if (selected)
      validateBundle(
        {
          ...b,
          knowledge_sim_ms: selected.knowledge_sim_ms,
          observations: [],
          scan_events: [],
          alerts: [],
          briefings: [],
          sensors: [],
          reports: [],
          metrics: [],
          events: [],
        },
        {
          knowledge_sim_ms: selected.knowledge_sim_ms,
          branch_id: branch,
          bundle: '',
        },
        manifest,
        base
      )
    combined.push(
      validateBundle(
        { ...b, cycle: null, zone_risk: [] },
        { knowledge_sim_ms: at, branch_id: branch, bundle: '' },
        manifest,
        base
      )
    )
  }
  const all = <K extends keyof CycleBundle>(key: K) =>
    combined.flatMap((b) => b[key] as never[]) as unknown as CycleBundle[K]
  const bundle = {
    schema_version: 2,
    bundle_id: `hub_${clock.active_branch_id}_${at}`,
    scenario_id: manifest.scenario_id,
    branch_id: clock.active_branch_id,
    knowledge_sim_ms: cycle?.knowledge_sim_ms ?? at,
    cycle,
    zone_risk: cycle
      ? (rows.zoneRisk ?? []).filter((r) => r.cycle_id === cycle.cycle_id)
      : [],
    observations: all('observations'),
    scan_events: all('scan_events'),
    alerts: all('alerts'),
    briefings: all('briefings'),
    sensors: combined[0]?.sensors ?? [],
    people: all('people'),
    reports: all('reports'),
    metrics: all('metrics'),
    events: all('events'),
  } as unknown as CycleBundle
  return { clock, now, view, branches, cycles, cycle, bundle }
}
