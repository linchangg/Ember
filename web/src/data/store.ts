import type { DataSource, Snapshot } from './source.ts'
import { FileSource } from './fileSource.ts'

/** Explicit selection. Importing this module never starts requests or changes the UI. */
export function createConfiguredSource(
  env: Record<string, string | undefined>,
  pageUrl: string
): DataSource {
  if (env.VITE_DATA_SOURCE !== 'file')
    throw new Error(
      'Phase B supports file mode only; hub/mock adapters must be selected explicitly later'
    )
  if (!env.VITE_ASSET_BASE || !env.VITE_SCENARIO_ID)
    throw new Error('File mode needs VITE_ASSET_BASE and VITE_SCENARIO_ID')
  return new FileSource({
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
