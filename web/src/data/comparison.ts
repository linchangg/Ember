import { useEffect, useState } from 'react'
import type { DataSource, Snapshot, BranchComparison } from './source.ts'
export interface ComparisonState {
  status: 'none' | 'loading' | 'ready' | 'unavailable' | 'error'
  data: BranchComparison | null
  error: string | null
  parent: string | null
}
export function useBranchComparison(
  source: DataSource,
  snapshot: Snapshot
): ComparisonState {
  const branch = snapshot.branches.find(
    (b) => b.branch_id === snapshot.clock?.active_branch_id
  )
  const parent = branch?.parent_id ?? null
  const cycle = snapshot.cycle
  const issue = cycle?.knowledge_sim_ms ?? 0
  const view = snapshot.view.viewTime
  const eligible =
    !!parent &&
    snapshot.status === 'ready' &&
    snapshot.forecastStatus === 'available' &&
    cycle?.branch_id === branch?.branch_id
  const key = JSON.stringify([
    parent,
    cycle?.cycle_id,
    view,
    snapshot.clock?.epoch,
    eligible,
  ])
  const [result, setResult] = useState<{
    key: string
    data: BranchComparison | null
    error: string | null
  } | null>(null)
  useEffect(() => {
    if (!eligible || !parent || !source.compareBranch) return
    const controller = new AbortController()
    void source
      .compareBranch(parent, issue, view, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setResult({ key, data, error: null })
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          setResult({
            key,
            data: null,
            error: error instanceof Error ? error.message : String(error),
          })
      })
    return () => controller.abort()
  }, [source, key, eligible, parent, issue, view])
  if (!parent) return { status: 'none', data: null, error: null, parent }
  if (!eligible || !source.compareBranch)
    return {
      status: 'unavailable',
      data: null,
      error: 'No child forecast or comparison adapter at this issue.',
      parent,
    }
  if (!result || result.key !== key)
    return { status: 'loading', data: null, error: null, parent }
  return {
    status: result.error ? 'error' : result.data ? 'ready' : 'unavailable',
    data: result.data,
    error: result.error,
    parent,
  }
}
