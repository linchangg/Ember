// Writes public/data/official_<scenario>.json: the first official evacuation alert (warning or order) per zone,
// from the engine's truth file. The dashboard compares it with Emberline's own alerts.
//   node scripts/export_official_orders.mjs [scenario]    (default: eaton)
import { readFileSync, writeFileSync } from 'node:fs'

const scn = process.argv[2] ?? 'eaton'
const root = new URL('../../', import.meta.url)
const truth = JSON.parse(readFileSync(new URL(`data/scenarios/${scn}/truth/zone_orders.json`, root)))
const first = new Map()
for (const e of truth.entries) {
  const cur = first.get(e.zone_id)
  if (!cur || e.t_sim_ms < cur.t_sim_ms) first.set(e.zone_id, e)
}
const out = {
  scenario_id: scn,
  description: 'First official evacuation warning or order per zone (advisories excluded).',
  sources: Object.values(truth.sources).map((s) => ({ title: s.title, url: s.url })),
  orders: [...first.values()]
    .map((e) => ({ zone_id: e.zone_id, level: e.official_level, t_sim_ms: e.t_sim_ms, local_pst: e.local_pst }))
    .sort((a, b) => a.t_sim_ms - b.t_sim_ms),
}
writeFileSync(new URL(`web/public/data/official_${scn}.json`, root), JSON.stringify(out))
console.log(`${out.orders.length} zones with an official alert -> web/public/data/official_${scn}.json`)
