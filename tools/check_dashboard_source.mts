/** Phase B HTTP acceptance: actual FileSource, without connecting a renderer. */
import assert from 'node:assert/strict'
import { FileSource } from '../web/src/data/fileSource.ts'
const base = process.argv[2] ?? 'http://127.0.0.1:8790/'
const scenario = process.argv[3] ?? 'rust-fixture'
let indexResponse: Response | undefined
// The CI host starts concurrently; wait only for a bounded local startup interval.
for (let attempt = 0; attempt < 30; attempt++) {
  try {
    indexResponse = await fetch(
      new URL(`cache/${scenario}/main/index.json`, base),
      { signal: AbortSignal.timeout(2000) }
    )
    break
  } catch (error) {
    if (attempt === 29) throw error
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
}
assert(indexResponse?.ok)
const index = await indexResponse.json()
const source = new FileSource({ assetBase: base, scenario, autoTick: false })
let forecasts = 0,
  preignition = 0
try {
  await source.initialize()
  for (const entry of index.cycles) {
    await source.actions.seek(entry.knowledge_sim_ms)
    const s = source.getSnapshot()
    assert.equal(s.status, 'ready')
    assert.equal(s.bundle!.knowledge_sim_ms, entry.knowledge_sim_ms)
    assert(s.observations.every((o) => o.available_sim_ms <= s.simTime))
    if (s.cycle) {
      forecasts++
      assert.equal(
        s.mapAssets!.arrival.byteLength,
        5 * s.manifest!.grid.nx * s.manifest!.grid.ny
      )
      assert(s.zoneRisk.every((r) => r.cycle_id === s.cycle!.cycle_id))
    } else {
      preignition++
      assert.equal(s.mapAssets, null)
    }
  }
  const s = source.getSnapshot(),
    observations = s.observations.length,
    alerts = s.alerts.length
  await source.actions.setScrub(s.cycle!.knowledge_sim_ms + 120 * 60000)
  assert.equal(source.getSnapshot().cycle!.cycle_id, s.cycle!.cycle_id)
  await source.actions.seek(index.cycles[0].knowledge_sim_ms)
  assert.equal(source.getSnapshot().cycle, null)
  assert.equal(source.getSnapshot().zoneRisk.length, 0)
  console.log(
    JSON.stringify(
      {
        phase: 'B',
        source: 'FileSource',
        transport: 'HTTP',
        scenario,
        forecast_cycles: forecasts,
        preignition_cycles: preignition,
        observations,
        alerts,
        atomic_cycles: true,
        backward_seek: true,
        renderer_connected: false,
      },
      null,
      2
    )
  )
} finally {
  source.dispose()
}
