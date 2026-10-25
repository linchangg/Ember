import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { FileSource } from '../data/fileSource.ts'
import { createPanelSelector, createConfiguredSource } from '../data/store.ts'
import { panelModel, displayProbability, displayTime } from './panelModel.ts'
import type { Snapshot } from '../data/source.ts'
import type { Branch } from '../contracts.ts'
const root =
  process.env.EMBERLINE_FIXTURE_DIR ??
  fileURLToPath(new URL('../../../fixtures/', import.meta.url))
const scenario = process.env.EMBERLINE_SCENARIO_ID ?? 'fixture'
const base = 'https://fixture.test/'
const index = JSON.parse(
  await readFile(path.join(root, `cache/${scenario}/main/index.json`), 'utf8')
)
const branches: Branch[] | null = await readFile(
  path.join(root, 'branches.json'),
  'utf8'
)
  .then(JSON.parse)
  .catch(() => null)
let reads = 0
const fetcher: typeof fetch = async (input) => {
  reads++
  try {
    return new Response(
      await readFile(path.join(root, new URL(String(input)).pathname))
    )
  } catch {
    return new Response('missing', { status: 404 })
  }
}
async function source() {
  const s = new FileSource({
    assetBase: base,
    scenario,
    fetcher,
    autoTick: false,
    branches: branches ?? undefined,
  })
  await s.initialize()
  return s
}
// Compile the actual TSX component with the project's installed compiler; no new test runtime.
const location = new URL('./IncidentPanels.tsx', import.meta.url)
const componentJS = ts
  .transpileModule(await readFile(location, 'utf8'), {
    compilerOptions: {
      jsx: ts.JsxEmit.ReactJSX,
      module: ts.ModuleKind.ESNext,
      target: ts.ScriptTarget.ES2022,
    },
  })
  .outputText.replace(
    /from ["']([^"']+)["']/g,
    (_m, specifier: string) =>
      `from ${JSON.stringify(specifier.startsWith('.') ? new URL(specifier, location).href : import.meta.resolve(specifier))}`
  )
const { default: Panels } = await import(
  `data:text/javascript;base64,${Buffer.from(componentJS).toString('base64')}`
)
const none = { status: 'none', data: null, error: null, parent: null }
const render = (s: Snapshot) =>
  renderToStaticMarkup(
    createElement(Panels, {
      model: panelModel(s),
      comparison: none,
      decide: () => {},
    })
  )
test('every fixture selection renders panels from the same cycle as its map, across 30/120/360 leads', async () => {
  const s = await source()
  try {
    for (const e of index.cycles) {
      await s.actions.seek(e.knowledge_sim_ms)
      for (const lead of [30, 120, 360]) {
        await s.actions.setScrub(e.knowledge_sim_ms + lead * 60000)
        const snapshot = s.getSnapshot(),
          model = panelModel(snapshot),
          html = render(snapshot)
        if (!snapshot.cycle) {
          assert.equal(model.cycle, null)
          assert.equal(model.rows.length, 0)
          assert.match(html, /No forecast yet/)
          continue
        }
        assert.equal(model.cycle!.cycle_id, snapshot.cycle.cycle_id)
        for (const row of model.rows) {
          assert.equal(row.cycle_id, snapshot.cycle.cycle_id)
          assert.equal(row.probability, row.p_by_lead[lead / 30 - 1])
          assert(html.includes(row.name))
          assert(html.includes(displayProbability(row.probability)))
        }
        assert(html.includes(snapshot.cycle.model_version))
        assert(html.includes('Tempering'))
      }
    }
  } finally {
    s.dispose()
  }
})
test('zero probability and zero heading remain values; missing ETA, configuration and non-published leads remain unavailable', async () => {
  const s = await source()
  try {
    await s.actions.seek(index.cycles.at(-1).knowledge_sim_ms)
    const snapshot = structuredClone(s.getSnapshot())
    snapshot.cycle!.head_bearing_deg = 0
    snapshot.zoneRisk[0].p_by_lead[3] = 0
    snapshot.zoneRisk[0].eta_q50_sim_ms = null
    snapshot.view.viewTime = snapshot.cycle!.knowledge_sim_ms + 120 * 60000
    assert.equal(panelModel(snapshot).rows[0].probability, 0)
    const html = render(snapshot)
    assert.match(html, /0%/)
    assert.match(html, /0° true/)
    assert.match(html, /Unavailable/)
    assert(!html.includes('Jan 1'))
    snapshot.view.viewTime = snapshot.cycle!.knowledge_sim_ms + 31 * 60000
    assert(panelModel(snapshot).rows.every((r) => r.probability === null))
    assert.equal(displayProbability(undefined), 'Unavailable')
    assert.equal(displayTime(null), 'Unavailable')
  } finally {
    s.dispose()
  }
})
test('loading, errors, horizon overflow and mixed-cycle risk do not leak forecast panel numbers', async () => {
  const s = await source()
  try {
    await s.actions.seek(index.cycles.at(-1).knowledge_sim_ms)
    const a = s.getSnapshot()
    for (const status of ['loading', 'error'] as const) {
      const snap = { ...a, status, error: 'test failure' }
      assert.equal(panelModel(snap).cycle, null)
      assert.equal(panelModel(snap).rows.length, 0)
      assert.equal(panelModel(snap).alerts.length, 0)
      assert(!render(snap).includes('data-testid="zone-risk"'))
    }
    assert.equal(
      panelModel({ ...a, forecastStatus: 'beyond-horizon' }).cycle,
      null
    )
    const wrong = structuredClone(a)
    wrong.zoneRisk[0].cycle_id = 'wrong'
    assert(!panelModel(wrong).rows.some((r) => r.cycle_id === 'wrong'))
  } finally {
    s.dispose()
  }
})
test('observation/event provenance stays causal and local review never submits a network request', async () => {
  const s = await source()
  try {
    await s.actions.seek(index.cycles.at(-1).knowledge_sim_ms)
    await s.actions.setScrub(
      s.getSnapshot().cycle!.knowledge_sim_ms + 120 * 60000
    )
    const a = s.getSnapshot(),
      model = panelModel(a)
    assert(model.observations.every((o) => o.available_sim_ms <= a.simTime))
    const markup = render(a)
    for (const o of model.observations) {
      assert(markup.includes(o.sensor_id))
      assert(markup.includes(o.provenance))
    }
    const proposal = a.alerts.find((r) => r.state === 'proposed')
    assert(proposal)
    const before = reads
    await s.actions.approve(proposal.alert_id)
    assert.equal(reads, before)
    assert.equal(
      s.getSnapshot().alerts.find((r) => r.alert_id === proposal.alert_id)!
        .state,
      'approved'
    )
    await s.actions.seek(index.cycles[0].knowledge_sim_ms)
    assert.equal(panelModel(s.getSnapshot()).rows.length, 0)
    await s.actions.seek(index.cycles.at(-1).knowledge_sim_ms)
    assert.equal(
      s.getSnapshot().alerts.find((r) => r.alert_id === proposal.alert_id)!
        .state,
      'approved'
    )
  } finally {
    s.dispose()
  }
})
test('panel selector is stable within a minute and updates on minute, cycle and error boundaries', async () => {
  const s = await source()
  try {
    const a = s.getSnapshot(),
      select = createPanelSelector()
    assert.strictEqual(select(a), a)
    assert.strictEqual(
      select({
        ...a,
        simTime: a.simTime + 1,
        view: { ...a.view, viewTime: a.view.viewTime + 1 },
      }),
      a
    )
    const minute = { ...a, simTime: a.simTime + 60000 }
    assert.strictEqual(select(minute), minute)
    const error = { ...minute, status: 'error' as const }
    assert.strictEqual(select(error), error)
  } finally {
    s.dispose()
  }
})
test('branch configuration rejects malformed descriptors without changing frozen contracts', () => {
  assert.throws(
    () =>
      createConfiguredSource(
        {
          VITE_DATA_SOURCE: 'file',
          VITE_ASSET_BASE: base,
          VITE_SCENARIO_ID: scenario,
          VITE_BRANCHES_JSON: '{}',
        },
        base
      ),
    /branch/
  )
  assert.throws(() =>
    createConfiguredSource(
      {
        VITE_DATA_SOURCE: 'file',
        VITE_ASSET_BASE: base,
        VITE_SCENARIO_ID: scenario,
        VITE_BRANCHES_JSON:
          '[{"reason":"missing identity","created_wall_ms":0}]',
      },
      base
    )
  )
})
test(
  'Rust-generated report branch compares at an exact issue and lead without mutating either selection or parent',
  { skip: !branches },
  async () => {
    const s = await source()
    try {
      const issue = index.cycles.at(-1).knowledge_sim_ms
      await s.actions.seek(issue)
      await s.actions.setScrub(issue + 120 * 60000)
      const parent = s.getSnapshot(),
        before = parent.mapAssets!.arrival.slice()
      await s.actions.switchBranch('report-demo')
      const child = s.getSnapshot()
      assert.equal(child.cycle!.branch_id, 'report-demo')
      const inherited = child.alerts.find(
        (a) => a.branch_id === 'main' && a.state === 'proposed'
      )
      assert(
        inherited,
        'parent proposal state must not inherit a post-fork supersession'
      )
      await assert.rejects(
        s.actions.approve(inherited.alert_id),
        /Switch to the alert branch/
      )
      assert(
        child.observations.some(
          (o) => o.kind === 'ground_report' && o.branch_id === 'report-demo'
        )
      )
      const comparison = await s.compareBranch(
        'main',
        issue,
        issue + 120 * 60000,
        new AbortController().signal
      )
      assert(comparison)
      assert.equal(
        comparison.cycle.knowledge_sim_ms,
        child.cycle!.knowledge_sim_ms
      )
      assert.deepEqual(comparison.mapAssets.arrival, before)
      assert.strictEqual(s.getSnapshot(), child)
      const report = child.observations.find((o) => o.kind === 'ground_report')!
      await s.actions.seek(report.available_sim_ms - 300000)
      assert(
        !s.getSnapshot().observations.some((o) => o.obs_id === report.obs_id)
      )
      await s.actions.seek(issue)
      await s.actions.switchBranch('main')
      assert.deepEqual(s.getSnapshot().mapAssets!.arrival, before)
      assert.equal(
        await s.compareBranch(
          'report-demo',
          index.cycles[4].knowledge_sim_ms,
          index.cycles[4].knowledge_sim_ms,
          new AbortController().signal
        ),
        null
      )
    } finally {
      s.dispose()
    }
  }
)

test('a retired source exposes a terminal snapshot to late React subscriptions without resurrecting', async () => {
  const s = await source()
  s.dispose()
  const unsubscribe = s.subscribe(() => assert.fail('retired source published'))
  assert.equal(s.getSnapshot().status, 'disposed')
  unsubscribe()
  await assert.rejects(s.initialize(), /disposed/)
})
