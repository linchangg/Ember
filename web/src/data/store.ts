import type { DataSource, Snapshot } from './source.ts'
import { FileSource } from './fileSource.ts'
import { SpacetimeSource } from './spacetimeSource.ts'

/** Explicit selection. Importing this module never starts requests or changes the UI. */
export function createConfiguredSource(
  env: Record<string, string | undefined>,
  pageUrl: string
): DataSource {
  if (env.VITE_DATA_SOURCE === 'stdb') {
    if (
      !env.VITE_STDB_URI ||
      !env.VITE_STDB_DB ||
      !env.VITE_ASSET_BASE ||
      !env.VITE_SCENARIO_ID
    )
      throw new Error(
        'Hub mode needs VITE_STDB_URI, VITE_STDB_DB, VITE_ASSET_BASE and VITE_SCENARIO_ID'
      )
    return new SpacetimeSource({
      uri: env.VITE_STDB_URI,
      database: env.VITE_STDB_DB,
      assetBase: env.VITE_ASSET_BASE,
      scenario: env.VITE_SCENARIO_ID,
      pageUrl,
    })
  }
  if (env.VITE_DATA_SOURCE !== 'file')
    throw new Error(
      'Phase B supports file mode only; hub/mock adapters must be selected explicitly later'
    )
  if (!env.VITE_ASSET_BASE || !env.VITE_SCENARIO_ID)
    throw new Error('File mode needs VITE_ASSET_BASE and VITE_SCENARIO_ID')
  let branches
  if (env.VITE_BRANCHES_JSON) {
    branches = JSON.parse(env.VITE_BRANCHES_JSON)
    if (
      !Array.isArray(branches) ||
      !branches.length ||
      branches.some(
        (b) =>
          !b ||
          typeof b !== 'object' ||
          typeof b.reason !== 'string' ||
          !Number.isSafeInteger(b.created_wall_ms)
      )
    )
      throw new Error(
        'VITE_BRANCHES_JSON must contain complete branch descriptors'
      )
  }
  return new FileSource({
    branches,
    assetBase: env.VITE_ASSET_BASE,
    scenario: env.VITE_SCENARIO_ID,
    pageUrl,
  })
}

// A deferred release handles React StrictMode's setup/cleanup/setup sequence without
// leaking old sources when the provider switches to a different scenario/source.
const leases = new WeakMap<DataSource, { count: number; generation: number }>()
export function acquireSource(source: DataSource) {
  const lease = leases.get(source) ?? { count: 0, generation: 0 }
  leases.set(source, lease)
  lease.count++
  lease.generation++
  void source.initialize().catch(() => {
    /* FileSource exposes initialization errors in its snapshot. */
  })
  let released = false
  return () => {
    if (released) return
    released = true
    lease.count--
    const generation = ++lease.generation
    queueMicrotask(() => {
      if (lease.count === 0 && lease.generation === generation) {
        source.dispose()
        leases.delete(source)
      }
    })
  }
}

/** External-store selector cache: unchanged snapshots must return the same value. */
export function createSnapshotSelector<T>(
  source: DataSource,
  selector: (snapshot: Snapshot) => T
) {
  let previous: Snapshot | undefined, value: T
  return () => {
    const snapshot = source.getSnapshot()
    if (snapshot !== previous) {
      value = selector(snapshot)
      previous = snapshot
    }
    return value
  }
}

/** Minute-granularity selector cache for panels; outside React render ownership. */
export function createPanelSelector() {
  let previous: Snapshot | undefined
  const fields = [
    'status',
    'error',
    'manifest',
    'cycle',
    'mapAssets',
    'zoneRisk',
    'observations',
    'events',
    'alerts',
    'briefings',
    'reports',
    'sensors',
    'branches',
    'zones',
  ] as const
  return (s: Snapshot) => {
    if (
      previous &&
      fields.every((key) => previous![key] === s[key]) &&
      previous.clock?.active_branch_id === s.clock?.active_branch_id &&
      previous.clock?.epoch === s.clock?.epoch &&
      Math.floor(previous.view.viewTime / 60000) ===
        Math.floor(s.view.viewTime / 60000) &&
      Math.floor(previous.simTime / 60000) === Math.floor(s.simTime / 60000)
    )
      return previous
    previous = s
    return s
  }
}
