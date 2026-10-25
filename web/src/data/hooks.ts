/** Shared hooks for the connected forecast map and later panel consumers. */
import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useSyncExternalStore,
} from 'react'
import type { ReactNode } from 'react'
import type { Cycle } from '../contracts.ts'
import type { DataSource, Snapshot } from './source.ts'
import { acquireSource, createSnapshotSelector } from './store.ts'

const Context = createContext<DataSource | null>(null)
export function DataSourceProvider({
  source,
  children,
}: {
  source: DataSource
  children?: ReactNode
}) {
  useEffect(() => acquireSource(source), [source])
  return createElement(Context.Provider, { value: source }, children)
}
function useSource() {
  const source = useContext(Context)
  if (!source) throw new Error('Dashboard hooks require DataSourceProvider')
  return source
}
function useSelected<T>(selector: (snapshot: Snapshot) => T) {
  const source = useSource()
  const getSelected = useMemo(
    () => createSnapshotSelector(source, selector),
    [source, selector]
  )
  return useSyncExternalStore(source.subscribe, getSelected, getSelected)
}
const selectors = {
  manifest: (s: Snapshot) => s.manifest,
  clock: (s: Snapshot) => s.clock,
  view: (s: Snapshot) => s.view,
  now: (s: Snapshot) => s.simTime,
  cycles: (s: Snapshot) => s.cycles,
  zones: (s: Snapshot) => s.zones,
  risk: (s: Snapshot) => s.zoneRisk,
  alerts: (s: Snapshot) => s.alerts,
  briefings: (s: Snapshot) => s.briefings,
  observations: (s: Snapshot) => s.observations,
  scans: (s: Snapshot) => s.scans,
  sensors: (s: Snapshot) => s.sensors,
  people: (s: Snapshot) => s.people,
  reports: (s: Snapshot) => s.reports,
  events: (s: Snapshot) => s.events,
  metrics: (s: Snapshot) => s.metrics,
  branches: (s: Snapshot) => s.branches,
  overrides: (s: Snapshot) => s.evacOverrides,
  status: (s: Snapshot) => s.status,
  error: (s: Snapshot) => s.error,
  pending: (s: Snapshot) => s.pending,
  forecastStatus: (s: Snapshot) => s.forecastStatus,
}
export const useManifest = () => useSelected(selectors.manifest)
export const useClock = () => useSelected(selectors.clock)
export const useSimNow = () => useSelected(selectors.now)
export const useCycles = () => useSelected(selectors.cycles)
export const useZones = () => useSelected(selectors.zones)
export const useAlerts = () => useSelected(selectors.alerts)
export const useScanEvents = () => useSelected(selectors.scans)
export const useSensors = () => useSelected(selectors.sensors)
export const usePeople = () => useSelected(selectors.people)
/** Bundles provide report acceptance summaries, not full phone/report records. */
export const useReports = () => useSelected(selectors.reports)
export const useMetrics = () => useSelected(selectors.metrics)
export const useBranches = () => useSelected(selectors.branches)
export const useEvacOverrides = () => useSelected(selectors.overrides)
export function useActions() {
  return useSource().actions
}
export function useView() {
  const view = useSelected(selectors.view),
    actions = useActions()
  return useMemo(
    () => ({ ...view, setScrub: actions.setScrub, goLive: actions.goLive }),
    [view, actions]
  )
}
export function useCycleAt(time: number) {
  return useSelected(
    useCallback(
      (s: Snapshot) =>
        s.cycles.findLast(
          (c) => c.knowledge_sim_ms <= Math.min(time, s.simTime)
        ),
      [time]
    )
  )
}
export function useArrivalGrid(cycle?: Cycle) {
  return useSelected(
    useCallback(
      (s: Snapshot) =>
        cycle?.cycle_id === s.cycle?.cycle_id
          ? s.mapAssets?.arrival
          : undefined,
      [cycle]
    )
  )
}
export function useZoneRisk(cycleId?: string) {
  const rows = useSelected(selectors.risk)
  return useMemo(
    () => (cycleId ? rows.filter((r) => r.cycle_id === cycleId) : rows),
    [rows, cycleId]
  )
}
export function useBriefing(id?: string) {
  const rows = useSelected(selectors.briefings)
  return useMemo(() => rows.find((r) => r.briefing_id === id), [rows, id])
}
export function useObservations(sinceMs = -Infinity) {
  const rows = useSelected(selectors.observations)
  return useMemo(
    () => rows.filter((r) => r.available_sim_ms >= sinceMs),
    [rows, sinceMs]
  )
}
export function useEvents(limit = 80) {
  const rows = useSelected(selectors.events)
  return useMemo(() => {
    const count = Number.isFinite(limit) ? Math.max(0, Math.floor(limit)) : 80
    return count
      ? rows.slice(-count).map((r, i) => ({
          ...r,
          seq: Math.max(0, rows.length - count) + i + 1,
        }))
      : []
  }, [rows, limit])
}
export function useCycleHead(branchId?: string) {
  const rows = useCycles()
  return useMemo(() => {
    const c = rows.findLast((r) => !branchId || r.branch_id === branchId)
    return c
      ? {
          scenario_id: c.scenario_id,
          branch_id: c.branch_id,
          cycle_id: c.cycle_id,
          knowledge_sim_ms: c.knowledge_sim_ms,
          danger: c.danger,
          burned_ha: c.burned_ha,
        }
      : undefined
  }, [rows, branchId])
}
export function useDataStatus() {
  const status = useSelected(selectors.status),
    error = useSelected(selectors.error)
  const pending = useSelected(selectors.pending),
    forecastStatus = useSelected(selectors.forecastStatus)
  return useMemo(
    () => ({ status, error, pending, forecastStatus }),
    [status, error, pending, forecastStatus]
  )
}

export function useMapSelection() {
  const cycle = useSelected(useCallback((s: Snapshot) => s.cycle, []))
  const assets = useSelected(useCallback((s: Snapshot) => s.mapAssets, []))
  return useMemo(() => ({ cycle, assets }), [cycle, assets])
}
