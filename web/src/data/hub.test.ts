import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { adapt, lineage, selectHub } from './hubRows.ts'
import type { HubRows } from './hubRows.ts'
import { SpacetimeSource } from './spacetimeSource.ts'
import type { ScenarioManifest } from './source.ts'
const root = new URL('../../../fixtures/', import.meta.url)
const manifest = JSON.parse(
  await readFile(new URL('static/fixture/manifest.json', root), 'utf8')
) as ScenarioManifest
const index = JSON.parse(
  await readFile(new URL('cache/fixture/main/index.json', root), 'utf8')
)
const b = JSON.parse(
  await readFile(new URL(index.cycles.at(-1).bundle, root), 'utf8')
)
const at = b.knowledge_sim_ms
function rows(): HubRows {
  return {
    clock: [
      {
        scenario_id: 'fixture',
        mode: 'replay',
        playing: false,
        speed: 240,
        anchor_sim_ms: at,
        anchor_wall_ms: 0,
        active_branch_id: 'main',
        status: 'ok',
        resume_playing: false,
        pause_on_proposal: false,
        epoch: 0,
        min_sim_ms: manifest.replay_start_sim_ms,
        max_sim_ms: manifest.replay_end_sim_ms,
      },
    ],
    branch: [
      {
        scenario_id: 'fixture',
        branch_id: 'main',
        parent_id: null,
        fork_sim_ms: null,
        reason: 'test',
        created_wall_ms: 0,
      },
    ],
    cycle: [b.cycle],
    cycleHead: [
      { branch_id: 'main', knowledge_sim_ms: at, cycle_id: b.cycle.cycle_id },
    ],
    zoneRisk: b.zone_risk,
    observation: b.observations,
    scanEvent: b.scan_events,
    alert: b.alerts,
    briefing: b.briefings,
    sensor: b.sensors,
    report: b.reports,
    metric: b.metrics,
    activityEvent: b.events,
  }
}
const base = new URL('http://assets.test/')
test('SDK options, JSON and camelCase normalize at one boundary', () => {
  assert.deepEqual(
    adapt({
      cycleId: 'x',
      assetsJson: '{"a":1}',
      configHash: undefined,
      n: 3n,
    }),
    { cycle_id: 'x', assets: { a: 1 }, config_hash: null, n: 3 }
  )
  assert.throws(() => adapt({ assetsJson: 'bad' }))
})
test('lineage takes all ancestor fork minima and rejects missing/cyclic ancestors', () => {
  const branches = rows().branch as never[]
  const child = {
    scenario_id: 'fixture',
    branch_id: 'child',
    parent_id: 'main',
    fork_sim_ms: at - 300000,
    reason: 'r',
    created_wall_ms: 0,
  }
  assert.equal(
    lineage([...branches, child], 'child', at).get('main'),
    at - 300000
  )
  assert.throws(() => lineage([child], 'child', at))
  assert.throws(() => lineage([{ ...child, parent_id: 'child' }], 'child', at))
})
test('committed heads, causal evidence and exact issue validation gate snapshots', () => {
  const r = rows()
  const s = selectHub(r, manifest, base, 0, at + 120 * 60000)
  assert.equal(s.cycle?.cycle_id, b.cycle.cycle_id)
  assert.deepEqual(s.bundle.zone_risk, b.zone_risk)
  r.cycleHead = []
  assert.equal(selectHub(r, manifest, base, 0, null).cycle, null)
  r.clock[0].anchor_sim_ms = manifest.replay_start_sim_ms
  const past = selectHub(r, manifest, base, 0, null)
  assert.equal(past.bundle.observations.length, 0)
  assert.equal(past.bundle.alerts.length, 0)
})
test('wrong model metadata fails closed before asset staging', () => {
  const broken = rows()
  broken.cycleHead[0].cycle_id = 'missing'
  assert.throws(
    () => selectHub(broken, manifest, base, 0, null),
    /missing cycle/
  )

  const r = rows()
  r.cycle = [{ ...b.cycle, data_through_sim_ms: at + 1 }]
  assert.throws(() => selectHub(r, manifest, base, 0, null), /metadata/)
})
const fixtureFetch: typeof fetch = async (input) =>
  new Response(
    await readFile(
      fileURLToPath(new URL(new URL(String(input)).pathname.slice(1), root))
    )
  )
const ready = async (s: SpacetimeSource) => {
  for (let i = 0; i < 200; i++) {
    if (s.getSnapshot().status === 'ready') return
    await new Promise((r) => setTimeout(r, 5))
  }
  throw new Error(s.getSnapshot().error ?? 'not ready')
}
test('hub assets commit coherently and actions never update local approval optimistically', async () => {
  let changed!: (r: HubRows) => void
  const calls: unknown[] = []
  const s = new SpacetimeSource({
    uri: 'http://hub.test',
    database: 'test-db',
    scenario: 'fixture',
    assetBase: base.href,
    fetcher: fixtureFetch,
    autoTick: false,
    now: () => 0,
    connect: (_u, _d, _s, onRows, _onError, onReady) => {
      changed = onRows
      queueMicrotask(() => {
        onReady()
        onRows(rows())
      })
      return {
        close() {},
        async call(name, args) {
          calls.push([name, args])
        },
      }
    },
  })
  await s.initialize()
  await ready(s)
  assert.equal(s.getSnapshot().cycle?.cycle_id, b.cycle.cycle_id)
  await assert.rejects(s.actions.pause(), /PIN/)
  s.configureOperator('test-pin', 'Test')
  await s.actions.pause()
  assert.equal(calls.length, 1)
  assert.equal(s.getSnapshot().clock?.playing, false)
  const r = rows()
  r.clock[0].epoch = 1
  r.clock[0].anchor_sim_ms = manifest.replay_start_sim_ms
  changed(r)
  await ready(s)
  assert.equal(s.getSnapshot().cycle, null)
  s.dispose()
  assert.equal(s.getSnapshot().status, 'disposed')
})
test('disconnect clears rendered availability and rejects commands; disposal prevents late rows', async () => {
  let fail!: (e: Error) => void, changed!: (r: HubRows) => void
  const s = new SpacetimeSource({
    uri: 'http://hub.test',
    database: 'test-db',
    scenario: 'fixture',
    assetBase: base.href,
    fetcher: fixtureFetch,
    autoTick: false,
    connect: (_u, _d, _s, onRows, onError, onReady) => {
      changed = onRows
      fail = onError
      queueMicrotask(() => {
        onReady()
        onRows(rows())
      })
      return { close() {}, async call() {} }
    },
  })
  await s.initialize()
  await ready(s)
  fail(new Error('offline'))
  assert.equal(s.getSnapshot().status, 'error')
  assert.equal(s.getSnapshot().mapAssets, null)
  s.configureOperator('pin', 'Test')
  await assert.rejects(s.actions.play(), /synchronized/)
  s.dispose()
  changed(rows())
  assert.equal(s.getSnapshot().status, 'disposed')
})
test('a slow superseded asset cannot overwrite a newer epoch', async () => {
  let changed!: (r: HubRows) => void, release!: () => void, waiting!: () => void
  const started = new Promise<void>((r) => {
      waiting = r
    }),
    gate = new Promise<void>((r) => {
      release = r
    })
  const fetcher: typeof fetch = async (input, init) => {
    if (String(input).endsWith('arrival_q.bin')) {
      waiting()
      await gate
    }
    return fixtureFetch(input, init)
  }
  const source = new SpacetimeSource({
    uri: 'http://hub.test',
    database: 'test-db',
    scenario: 'fixture',
    assetBase: base.href,
    fetcher,
    autoTick: false,
    connect: (_u, _d, _s, onRows, _error, onReady) => {
      changed = onRows
      queueMicrotask(() => {
        onReady()
        onRows(rows())
      })
      return { close() {}, async call() {} }
    },
  })
  await source.initialize()
  await started
  const next = rows()
  next.clock[0].epoch = 1
  next.clock[0].anchor_sim_ms = manifest.replay_start_sim_ms
  changed(next)
  await ready(source)
  release()
  await new Promise((r) => setTimeout(r, 20))
  assert.equal(source.getSnapshot().clock?.epoch, 1)
  assert.equal(source.getSnapshot().cycle, null)
  assert.equal(source.getSnapshot().mapAssets, null)
  source.dispose()
})
