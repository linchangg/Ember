/** Two independent real SDK clients against a disposable published local hub. No UI automation. */
import { createServer } from '../web/node_modules/vite/dist/node/index.js'
import { readFile, writeFile } from 'node:fs/promises'
import assert from 'node:assert/strict'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { SpacetimeSource } from '../web/src/data/spacetimeSource.ts'
const hubUri = process.env.STDB_URI ?? 'http://127.0.0.1:3017'
if (!['127.0.0.1', 'localhost', '[::1]'].includes(new URL(hubUri).hostname))
  throw new Error('Acceptance writes require a disposable local hub')
const credentials = JSON.parse(
  await readFile(process.env.EMBER_HUB_CREDENTIALS!, 'utf8')
)
const server = await createServer({
  root: new URL('../web', import.meta.url).pathname,
  server: { middlewareMode: true },
  appType: 'custom',
})
const { connectHub } = await server.ssrLoadModule('/src/data/hubConnection.ts')
const sources = [0, 1].map(
  () =>
    new SpacetimeSource({
      uri: process.env.STDB_URI ?? 'http://127.0.0.1:3017',
      database: process.env.STDB_DB ?? 'phase-e-demo',
      scenario: process.env.EMBER_SCENARIO_ID ?? 'rust-fixture',
      assetBase: process.env.EMBER_ASSET_BASE ?? 'http://127.0.0.1:8794/',
      connect: connectHub,
    })
)
const wait = async (fn: () => boolean, detail: string) => {
  const end = Date.now() + 20000
  while (!fn()) {
    if (Date.now() > end)
      throw new Error(
        `${detail}: ${sources.map((s) => JSON.stringify({ status: s.getSnapshot().status, error: s.getSnapshot().error })).join(' ')}`
      )
    await new Promise((r) => setTimeout(r, 25))
  }
}
try {
  await Promise.all(sources.map((s) => s.initialize()))
  await wait(
    () =>
      sources.every(
        (s) => s.getSnapshot().status === 'ready' && !!s.getSnapshot().cycle
      ),
    'initial snapshot'
  )
  assert.equal(
    sources[0].getSnapshot().cycle!.cycle_id,
    sources[1].getSnapshot().cycle!.cycle_id
  )
  for (const s of sources)
    s.configureOperator(credentials.OPERATOR_PIN, 'Phase E test')
  await sources[0].actions.setScrub(
    sources[0].getSnapshot().cycle!.knowledge_sim_ms + 120 * 60000
  )
  const alert = sources[0]
    .getSnapshot()
    .alerts.find((a) => a.state === 'proposed')
  assert.ok(alert, 'a real fixture proposal is required')
  await sources[0].actions.approve(alert.alert_id)
  await wait(
    () =>
      sources.every(
        (s) =>
          s.getSnapshot().alerts.find((a) => a.alert_id === alert.alert_id)
            ?.state === 'approved'
      ),
    'shared approval'
  )
  await assert.rejects(sources[1].actions.approve(alert.alert_id))
  const cycle = sources[0].getSnapshot().cycle!.cycle_id
  if (process.env.EMBER_HUB_RESTART_READY) {
    await writeFile(process.env.EMBER_HUB_RESTART_READY, 'ready')
    await wait(
      () => sources[0].getSnapshot().status === 'error',
      'server stopped'
    )
    await wait(
      () => sources[0].getSnapshot().status === 'ready',
      'automatic restart resubscription'
    )
    assert.equal(
      sources[0].getSnapshot().alerts.find((a) => a.alert_id === alert.alert_id)
        ?.state,
      'approved'
    )
  }
  sources[1].dispose()
  const reconnected = new SpacetimeSource({
    uri: process.env.STDB_URI ?? 'http://127.0.0.1:3017',
    database: process.env.STDB_DB ?? 'phase-e-demo',
    scenario: process.env.EMBER_SCENARIO_ID ?? 'rust-fixture',
    assetBase: process.env.EMBER_ASSET_BASE ?? 'http://127.0.0.1:8794/',
    connect: connectHub,
  })
  sources[1] = reconnected
  await reconnected.initialize()
  await wait(
    () => reconnected.getSnapshot().status === 'ready',
    'full reconnect'
  )
  assert.equal(reconnected.getSnapshot().cycle!.cycle_id, cycle)
  assert.equal(
    reconnected.getSnapshot().alerts.find((a) => a.alert_id === alert.alert_id)
      ?.state,
    'approved'
  )
  for (const s of sources) {
    const ids = s.getSnapshot().events.map((e) => e.event_id)
    assert.equal(new Set(ids).size, ids.length)
    assert.equal(ids.filter((id) => id === 'ev_phase_e_idempotence').length, 1)
  }
  let branchReview = false
  if (process.env.EMBER_FIXTURE_DIR) {
    const root = process.env.EMBER_FIXTURE_DIR
    const branches = JSON.parse(
      await readFile(join(root, 'branches.json'), 'utf8')
    )
    const child = branches.find(
      (b: { branch_id: string }) => b.branch_id === 'report-demo'
    )
    assert.ok(child)
    const call = async (reducer: string, arg: unknown) => {
      const response = await fetch(
        `${hubUri}/v1/database/${process.env.STDB_DB ?? 'phase-e-demo'}/call/${reducer}`,
        {
          method: 'POST',
          headers: {
            'content-type': 'application/json',
            authorization: `Bearer ${credentials.STDB_TOKEN}`,
          },
          body: JSON.stringify([JSON.stringify(arg)]),
        }
      )
      assert.equal(
        response.status,
        200,
        `Engine test reducer ${reducer} failed`
      )
    }
    await call('create_branch', child)
    const index = JSON.parse(
      await readFile(
        join(root, `cache/${child.scenario_id}/report-demo/index.json`),
        'utf8'
      )
    )
    for (let i = 0; i < index.cycles.length; i += 10) {
      const bundles = await Promise.all(
        index.cycles
          .slice(i, i + 10)
          .map(async (e: { bundle: string }) =>
            JSON.parse(await readFile(join(root, e.bundle), 'utf8'))
          )
      )
      await call('publish_bundles', bundles)
    }
    const latest = index.cycles.at(-1).knowledge_sim_ms
    await wait(
      () =>
        sources.every(
          (s) =>
            s.getSnapshot().status === 'ready' &&
            s.getSnapshot().clock?.active_branch_id === 'report-demo' &&
            s.getSnapshot().cycle?.branch_id === 'report-demo' &&
            s.getSnapshot().cycle?.knowledge_sim_ms === latest
        ),
      'shared engine-owned branch'
    )
    const before = sources[0].getSnapshot()
    const comparison = await sources[0].compareBranch(
      'main',
      latest,
      latest + 120 * 60000,
      new AbortController().signal
    )
    assert.ok(comparison)
    assert.equal(comparison.cycle.cycle_id, cycle)
    assert.equal(
      sources[0].getSnapshot().cycle?.cycle_id,
      before.cycle?.cycle_id
    )
    assert.ok(
      before.observations.some(
        (o) => o.kind === 'ground_report' && o.provenance === 'simulated'
      )
    )
    branchReview = true
  }
  const result = {
    actual_published_hub: true,
    clients: 2,
    cycle,
    shared_approval: true,
    shared_engine_branch_and_exact_parent_review: branchReview,
    duplicate_review_rejected: true,
    reconnect_preserves_approval: true,
    duplicate_events: 0,
    automatic_server_restart: !!process.env.EMBER_HUB_RESTART_READY,
    notification_worker_started: false,
  }
  await writeFile(
    process.env.EMBER_HUB_EVIDENCE ??
      join(tmpdir(), 'fire-phase-e-hub-evidence.json'),
    JSON.stringify(result, null, 2) + '\n'
  )
  console.log(JSON.stringify(result))
} finally {
  sources.forEach((s) => s.dispose())
  await server.close()
}
