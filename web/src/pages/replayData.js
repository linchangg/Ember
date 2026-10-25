import { useEffect, useState } from 'react'
import { assetRoot, assetUrl, readResponse } from '../data/assetUrl.ts'

// Optional replay files the engine writes next to the forecast cache (all read-only, all may be missing):
//   cache/<scn>/<branch>/index.json   which precomputed branches exist
//   cache/<scn>/<branch>/calls.json   the duty-officer call placed when an alert is approved
//   cache/<scn>/lead_times.json       per-zone alert times vs the official orders

/** The precomputed replay runs, as independent root branches. Labels say what data each run used. */
export const BRANCHES = [
  { id: 'main', label: 'Satellites + field reports' },
  { id: 'satellites-only', label: 'Satellites only' },
  { id: 'openloop', label: 'No live data' },
]
export const branchLabel = (id) => BRANCHES.find((b) => b.id === id)?.label ?? id

export const branchDescriptors = (scn) =>
  BRANCHES.map((b) => ({ scenario_id: scn, branch_id: b.id, parent_id: null, fork_sim_ms: null, reason: b.label, created_wall_ms: 0 }))

const root = () => assetRoot(import.meta.env.VITE_ASSET_BASE ?? import.meta.env.BASE_URL, window.location.href)
const cache = new Map()
/** JSON file under the asset root, or null if it is missing or not JSON (the dev server answers unknown paths with index.html). */
function fetchJson(rel) {
  if (!cache.has(rel)) cache.set(rel, readResponse(fetch, assetUrl(root(), rel), new AbortController().signal).catch(() => null))
  return cache.get(rel)
}

/** undefined while loading, then the parsed file or null. */
export function useReplayJson(rel) {
  const [state, setState] = useState({ rel: null, data: undefined })
  useEffect(() => {
    if (!rel) return undefined
    let alive = true
    fetchJson(rel).then((data) => alive && setState({ rel, data }))
    return () => { alive = false }
  }, [rel])
  return rel && state.rel === rel ? state.data : undefined
}

/** Ids of the branches that have their own index.json. The fixture has only main, so its switcher stays hidden. */
export function useAvailableBranches(scn) {
  const [ids, setIds] = useState([])
  useEffect(() => {
    let alive = true
    Promise.all(BRANCHES.map((b) => fetchJson(`cache/${scn}/${b.id}/index.json`).then((j) => (j?.branch_id === b.id && j?.scenario_id === scn ? b.id : null))))
      .then((found) => alive && setIds(found.filter(Boolean)))
    return () => { alive = false }
  }, [scn])
  return ids
}

/**
 * One comparison row per zone with an official warning or order: the order where there was an official order, else the warning.
 * lead_min > 0 means Emberline was earlier. Sorted earliest-first, zones with no Emberline alert last.
 */
export function leadRows(lead, branch, names) {
  const zones = lead?.branches?.[branch]?.zones ?? {}
  const rows = []
  for (const [id, z] of Object.entries(zones)) {
    const kind = z.official_order_sim_ms != null ? 'order' : z.official_warn_sim_ms != null ? 'warn' : null
    if (!kind) continue
    rows.push({ id, name: names.get(id) ?? id, kind, official: z[`official_${kind}_sim_ms`], ours: z[`${kind}_sim_ms`], lead: z[`${kind}_lead_min`] })
  }
  return rows.sort((a, b) => (b.lead ?? -Infinity) - (a.lead ?? -Infinity) || a.name.localeCompare(b.name))
}
