/** HTTP acceptance for Linda's scenario API; no second replay clock or renderer. */
import assert from 'node:assert/strict'
import { openScenario } from '../web/src/data/scenario.ts'
const base = process.argv[2] ?? 'http://127.0.0.1:8790/'
const scenario = process.argv[3] ?? 'rust-fixture'
for (let attempt = 0; ; attempt++) {
  try {
    const response = await fetch(
      new URL(`static/${scenario}/manifest.json`, base),
      { signal: AbortSignal.timeout(2000) }
    )
    assert(response.ok)
    break
  } catch (error) {
    if (attempt === 29) throw error
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
}
const source = await openScenario(scenario, { base })
let forecasts = 0,
  preignition = 0
const select = async (now: number, view = now) => {
  for (let attempt = 0; attempt < 500; attempt++) {
    const state = source.select(now, view)
    if (state.status !== 'loading') return state
    await new Promise((resolve) => setTimeout(resolve, 10))
  }
  throw new Error('Scenario selection timeout')
}
try {
  for (const item of source.cycles) {
    const state = await select(item.knowledge_sim_ms)
    assert.equal(state.issue, item.knowledge_sim_ms)
    if (state.status === 'not-issued') {
      preignition++
      assert.equal(state.entry, null)
      continue
    }
    assert.equal(state.status, 'ready', state.error ?? '')
    forecasts++
    const entry = state.entry!
    assert.equal(entry.bundle.knowledge_sim_ms, item.knowledge_sim_ms)
    assert(
      entry.bundle.observations.every(
        (o) => o.available_sim_ms <= item.knowledge_sim_ms
      )
    )
    for (const lead of [30, 120, 360]) {
      const view = item.knowledge_sim_ms + lead * 60000
      assert.equal(
        (await select(item.knowledge_sim_ms, view)).issue,
        item.knowledge_sim_ms
      )
      for (const q of ['probability', 0, 1, 2, 3, 4] as const)
        assert(source.raster(entry, view, q))
    }
  }
  assert.equal((await select(source.cycles[0].knowledge_sim_ms)).entry, null)
  assert.equal(forecasts, 36)
  assert.equal(preignition, 3)
  console.log(
    JSON.stringify(
      {
        source: 'Linda scenario API',
        transport: 'HTTP',
        scenario,
        forecast_cycles: forecasts,
        preignition_cycles: preignition,
        all_quantiles: true,
        leads_min: [30, 120, 360],
        backward_seek: true,
        atomic_assets: true,
        clock_owner: 'replayClock.js',
        renderer_checked: false,
      },
      null,
      2
    )
  )
} finally {
  source.dispose()
}
