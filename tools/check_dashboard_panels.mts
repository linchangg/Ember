/** Phase D acceptance over real HTTP; shares the exact UI view model. */
import assert from 'node:assert/strict'
import { FileSource } from '../web/src/data/fileSource.ts'
import { panelModel } from '../web/src/forecast/panelModel.ts'
const base = process.argv[2] ?? 'http://127.0.0.1:8794/'
const scenario = process.argv[3] ?? 'rust-fixture'
const branchResponse = await fetch(new URL('branches.json', base))
const branches = branchResponse.ok ? await branchResponse.json() : undefined
const source = new FileSource({
  assetBase: base,
  scenario,
  branches,
  autoTick: false,
})
let main = 0,
  children = 0,
  matched = 0
try {
  await source.initialize()
  const index = await fetch(
    new URL(`cache/${scenario}/main/index.json`, base)
  ).then((r) => r.json())
  for (const entry of index.cycles) {
    await source.actions.seek(entry.knowledge_sim_ms)
    if (!source.getSnapshot().cycle) continue
    for (const lead of [30, 120, 360]) {
      await source.actions.setScrub(entry.knowledge_sim_ms + lead * 60000)
      const s = source.getSnapshot(),
        m = panelModel(s)
      assert.equal(m.cycle!.cycle_id, s.cycle!.cycle_id)
      for (const row of m.rows) {
        assert.equal(row.cycle_id, s.cycle!.cycle_id)
        assert.equal(row.probability, row.p_by_lead[lead / 30 - 1])
      }
      assert(m.observations.every((o) => o.available_sim_ms <= s.simTime))
    }
    main++
  }
  for (const branch of branches ?? []) {
    if (!branch.parent_id) continue
    const index = await fetch(
      new URL(`cache/${scenario}/${branch.branch_id}/index.json`, base)
    ).then((r) => r.json())
    await source.actions.switchBranch(branch.branch_id)
    for (const entry of index.cycles) {
      await source.actions.seek(entry.knowledge_sim_ms)
      await source.actions.setScrub(entry.knowledge_sim_ms + 120 * 60000)
      const before = source.getSnapshot()
      const comparison = await source.compareBranch(
        branch.parent_id,
        entry.knowledge_sim_ms,
        before.view.viewTime,
        new AbortController().signal
      )
      assert(comparison)
      assert.equal(
        comparison.cycle.knowledge_sim_ms,
        before.cycle!.knowledge_sim_ms
      )
      assert.strictEqual(before, source.getSnapshot())
      children++
      matched++
    }
    assert(
      source
        .getSnapshot()
        .observations.some(
          (o) => o.kind === 'ground_report' && o.branch_id === branch.branch_id
        )
    )
  }
  console.log(
    JSON.stringify(
      {
        source: 'FileSource + Phase D panel model',
        transport: 'HTTP',
        main_forecasts: main,
        child_forecasts: children,
        exact_issue_comparisons: matched,
        leads_min: [30, 120, 360],
        causal_observations: true,
        parent_selection_unchanged: true,
        notifications_sent: 0,
        renderer_checked: false,
      },
      null,
      2
    )
  )
} finally {
  source.dispose()
}
