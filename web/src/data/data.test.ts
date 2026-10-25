import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createElement } from 'react'
import { renderToString } from 'react-dom/server'
import { FileSource } from './fileSource.ts'
import { assetRoot, assetUrl } from './assetUrl.ts'
import { ArrivalGridCache } from './arrivalGrid.ts'
import {
  validateIndex,
  validateManifest,
  validateBundle,
} from './validation.ts'
import { acquireSource, createConfiguredSource } from './store.ts'
import {
  DataSourceProvider,
  useManifest,
  useCycleAt,
  useArrivalGrid,
  useEvents,
  useZoneRisk,
} from './hooks.ts'
import type { Branch } from '../contracts.ts'

const root =
  process.env.EMBER_FIXTURE_DIR ??
  fileURLToPath(new URL('../../../fixtures/', import.meta.url))
const scenario = process.env.EMBER_SCENARIO_ID ?? 'fixture'
const base = assetRoot('https://fixtures.test/')
const manifestPath = `static/${scenario}/manifest.json`
const rawManifest = JSON.parse(
  await readFile(path.join(root, manifestPath), 'utf8')
)
const rawIndex = JSON.parse(
  await readFile(path.join(root, `cache/${scenario}/main/index.json`), 'utf8')
)
const entries = validateIndex(rawIndex, scenario, 'main', base)
const firstForecast = entries.find(
  (e) => e.knowledge_sim_ms > rawManifest.t_ignition_sim_ms
)!
const last = entries.at(-1)!
const readBundle = async (entry = last) =>
  JSON.parse(await readFile(path.join(root, entry.bundle), 'utf8'))
function gate() {
  let release!: () => void
  const promise = new Promise<void>((resolve) => {
    release = resolve
  })
  return { promise, release }
}
function harness(
  options: {
    edit?: (
      relative: string,
      data: Uint8Array
    ) => Uint8Array | Promise<Uint8Array>
    before?: (relative: string) => Promise<void>
    branches?: Branch[]
  } = {}
) {
  let wall = 1000000
  const calls: string[] = []
  const fetcher = (async (input: RequestInfo | URL, init?: RequestInit) => {
    assert.equal(init?.credentials, 'omit')
    assert.equal(init?.redirect, 'error')
    const relative = new URL(String(input)).pathname.slice(1)
    calls.push(relative)
    await options.before?.(relative)
    try {
      let bytes = new Uint8Array(await readFile(path.join(root, relative)))
      if (options.edit) bytes = await options.edit(relative, bytes)
      return new Response(bytes, { status: 200 })
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT')
        return new Response('missing', { status: 404 })
      throw error
    }
  }) as typeof fetch
  const source = new FileSource({
    assetBase: base.href,
    scenario,
    fetcher,
    now: () => wall,
    autoTick: false,
    branches: options.branches,
  })
  return {
    source,
    calls,
    fetcher,
    setWall: (value: number) => {
      wall = value
    },
  }
}

test('asset paths cannot traverse, change origin or bypass the asset prefix', () => {
  const confined = assetRoot('https://example.test/fixture-root/')
  assert.equal(
    assetUrl(confined, 'cache/a.bin'),
    'https://example.test/fixture-root/cache/a.bin'
  )
  for (const bad of [
    '../x',
    '%2e%2e/x',
    '%252e%252e/x',
    'https://evil.test/x',
    '//evil.test/x',
    '/x',
    'a\\x',
    'a?x',
    'a#x',
    '%2fx',
    'a/%2e/x',
  ])
    assert.throws(() => assetUrl(confined, bad))
})
test('manifest/index reject incompatible constants, duplicate timestamps and bad identity', () => {
  const m = structuredClone(rawManifest)
  m.quantiles = [0.5]
  assert.throws(() => validateManifest(m, scenario, base))
  const index = structuredClone(rawIndex)
  index.cycles.push(index.cycles[0])
  assert.throws(() => validateIndex(index, scenario, 'main', base), /Duplicate/)
  assert.throws(
    () => validateIndex(rawIndex, 'other', 'main', base),
    /identity/
  )
  const reversed = structuredClone(rawIndex)
  reversed.cycles.reverse()
  assert.deepEqual(validateIndex(reversed, scenario, 'main', base), entries)
})
test('pre-ignition initialization is paused with no forecast and exact bounds', async () => {
  const { source } = harness()
  await source.initialize()
  const s = source.getSnapshot()
  assert.equal(s.status, 'ready')
  assert.equal(s.clock!.playing, false)
  assert.equal(s.clock!.speed, 240)
  assert.equal(s.simTime, rawManifest.replay_start_sim_ms)
  assert.equal(s.forecastStatus, 'not-issued')
  assert.equal(s.cycle, null)
  assert.equal(s.mapAssets, null)
  assert.equal(s.zoneRisk.length, 0)
  source.dispose()
})
test('all fixture entries stage one consistent cycle/assets/risk and only eligible evidence', async () => {
  const { source } = harness()
  await source.initialize()
  for (const entry of entries) {
    await source.actions.seek(entry.knowledge_sim_ms)
    const s = source.getSnapshot()
    assert.equal(s.status, 'ready')
    assert.equal(s.bundle!.knowledge_sim_ms, entry.knowledge_sim_ms)
    for (const obs of s.observations) assert(obs.available_sim_ms <= s.simTime)
    if (s.cycle) {
      assert.equal(s.cycle.knowledge_sim_ms, entry.knowledge_sim_ms)
      assert.equal(
        s.mapAssets!.arrival.length,
        5 * rawManifest.grid.nx * rawManifest.grid.ny
      )
      assert(s.zoneRisk.every((r) => r.cycle_id === s.cycle!.cycle_id))
    } else assert.equal(s.mapAssets, null)
  }
  await source.actions.seek(entries[0].knowledge_sim_ms)
  assert.equal(source.getSnapshot().cycle, null)
  assert.equal(source.getSnapshot().mapAssets, null)
  assert.equal(source.getSnapshot().alerts.length, 0)
  source.dispose()
})
test('scrubbing changes view only; future views use the latest currently known cycle', async () => {
  const { source } = harness()
  await source.initialize()
  await source.actions.seek(last.knowledge_sim_ms)
  const anchor = source.getSnapshot().clock!.anchor_sim_ms
  await source.actions.setScrub(firstForecast.knowledge_sim_ms)
  assert.equal(
    source.getSnapshot().cycle!.knowledge_sim_ms,
    firstForecast.knowledge_sim_ms
  )
  assert.equal(source.getSnapshot().clock!.anchor_sim_ms, anchor)
  await source.actions.setScrub(last.knowledge_sim_ms + 120 * 60000)
  assert.equal(
    source.getSnapshot().cycle!.knowledge_sim_ms,
    last.knowledge_sim_ms
  )
  assert.equal(source.getSnapshot().forecastStatus, 'available')
  await source.actions.goLive()
  assert.equal(source.getSnapshot().view.mode, 'live')
  source.dispose()
})
test('no later cycle leaks when viewing the future from an earlier simulation issue', async () => {
  const { source } = harness()
  await source.initialize()
  await source.actions.seek(firstForecast.knowledge_sim_ms)
  await source.actions.setScrub(
    last.knowledge_sim_ms + rawManifest.horizon_min * 60000
  )
  const s = source.getSnapshot()
  assert.equal(s.cycle!.knowledge_sim_ms, firstForecast.knowledge_sim_ms)
  assert.equal(s.forecastStatus, 'beyond-horizon')
  assert.equal(s.mapAssets, null)
  source.dispose()
})
test('slow obsolete seek cannot replace a newer pre-ignition selection, even if fetch ignores abort', async () => {
  const entered = gate(),
    release = gate()
  const { source } = harness({
    before: async (relative) => {
      if (relative === firstForecast.bundle) {
        entered.release()
        await release.promise
      }
    },
  })
  await source.initialize()
  const old = source.actions.seek(last.knowledge_sim_ms)
  await entered.promise
  assert.equal(source.getSnapshot().status, 'loading')
  await source.actions.seek(entries[0].knowledge_sim_ms)
  release.release()
  await old
  assert.equal(
    source.getSnapshot().bundle!.knowledge_sim_ms,
    entries[0].knowledge_sim_ms
  )
  assert.equal(source.getSnapshot().cycle, null)
  source.dispose()
})
test('a broken binary clears selected forecast/panels and exposes an error instead of mock fallback', async () => {
  const selected = await readBundle()
  const { source } = harness({
    edit: (relative, bytes) =>
      relative === selected.cycle.assets.arrival_q ? bytes.slice(1) : bytes,
  })
  await source.initialize()
  await assert.rejects(
    source.actions.seek(last.knowledge_sim_ms),
    /byte length/
  )
  const s = source.getSnapshot()
  assert.equal(s.status, 'error')
  assert.equal(s.cycle, null)
  assert.equal(s.mapAssets, null)
  assert.equal(s.zoneRisk.length, 0)
  source.dispose()
})
test('future observations and unsupported bundle schema fail before commit', async () => {
  const original = await readBundle()
  const manifest = validateManifest(rawManifest, scenario, base)
  for (const update of [
    (b: any) => {
      b.schema_version = 99
    },
    (b: any) => {
      b.observations[0].available_sim_ms = last.knowledge_sim_ms + 1
    },
  ]) {
    const b = structuredClone(original)
    update(b)
    assert.throws(() => validateBundle(b, last, manifest, base))
  }
})
test('disposal cancels outstanding staging and prevents subsequent publication', async () => {
  const entered = gate(),
    release = gate()
  const { source } = harness({
    before: async (relative) => {
      if (relative === firstForecast.bundle) {
        entered.release()
        await release.promise
      }
    },
  })
  await source.initialize()
  const task = source.actions.seek(last.knowledge_sim_ms)
  await entered.promise
  source.dispose()
  release.release()
  await task
  assert.equal(source.getSnapshot().status, 'disposed')
  await assert.rejects(source.actions.play(), /disposed/)
})
test('clock play/pause/speed and reset use deterministic anchors and enforce bounds', async () => {
  const { source, setWall } = harness()
  await source.initialize()
  await source.actions.setPauseOnProposal(false)
  await source.actions.play()
  setWall(1001000)
  await source.tick()
  assert.equal(
    source.getSnapshot().simTime,
    rawManifest.replay_start_sim_ms + 240000
  )
  await source.actions.pause()
  setWall(1002000)
  await source.tick()
  assert.equal(
    source.getSnapshot().simTime,
    rawManifest.replay_start_sim_ms + 240000
  )
  await assert.rejects(source.actions.setSpeed(NaN))
  await assert.rejects(source.actions.seek(0))
  await source.actions.reset()
  assert.equal(source.getSnapshot().clock!.playing, false)
  assert.equal(source.getSnapshot().simTime, rawManifest.replay_start_sim_ms)
  source.dispose()
})
test('local approval survives seeks and cannot be downgraded or sent externally', async () => {
  const { source } = harness()
  await source.initialize()
  await source.actions.seek(last.knowledge_sim_ms)
  const proposal = source
    .getSnapshot()
    .alerts.find((a) => a.state === 'proposed')
  assert(proposal)
  await source.actions.approve(proposal.alert_id)
  await source.actions.seek(entries[0].knowledge_sim_ms)
  await source.actions.seek(last.knowledge_sim_ms)
  assert.equal(
    source.getSnapshot().alerts.find((a) => a.alert_id === proposal.alert_id)!
      .state,
    'approved'
  )
  await assert.rejects(
    source.actions.decline(proposal.alert_id, 'changed mind'),
    /proposal/
  )
  await assert.rejects(
    source.actions.injectTapReport(-118, 34, 'fire'),
    /assimilation/
  )
  const zone = source.getSnapshot().zones[0].properties.zone_id
  await source.actions.setEvacMin(zone, 45)
  assert.equal(source.getSnapshot().evacOverrides[zone], 45)
  source.dispose()
})
test('playback pauses when a new alert proposal is committed', async () => {
  const { source, setWall } = harness()
  await source.initialize()
  await source.actions.play()
  setWall(
    1000000 +
      Math.ceil((last.knowledge_sim_ms - rawManifest.replay_start_sim_ms) / 240)
  )
  await source.tick()
  assert.equal(source.getSnapshot().clock!.playing, false)
  assert(source.getSnapshot().alerts.some((a) => a.state === 'proposed'))
  source.dispose()
})
test('arrival cache holds at most four grids and does not reuse model identities', async () => {
  const { fetcher, calls } = harness()
  const manifest = validateManifest(rawManifest, scenario, base)
  const cache = new ArrivalGridCache(fetcher, base),
    signal = new AbortController().signal
  const forecasts = entries
    .filter((e) => e.knowledge_sim_ms > rawManifest.t_ignition_sim_ms)
    .slice(0, 5)
  for (const e of forecasts) {
    const b = await readBundle(e)
    await cache.load(b.cycle, manifest, signal)
  }
  assert.equal(cache.size, 4)
  const b = await readBundle(forecasts.at(-1))
  const before = calls.length
  await cache.load(b.cycle, manifest, signal)
  assert.equal(calls.length, before)
  await cache.load(
    { ...b.cycle, model_version: 'different-model' },
    manifest,
    signal
  )
  assert.equal(calls.length, before + 3)
  cache.clear()
  assert.equal(cache.size, 0)
})
test('configuration is explicit and has no mock/hub silent fallback', () => {
  assert.throws(
    () => createConfiguredSource({}, 'http://localhost/'),
    /file mode/
  )
  assert.throws(
    () =>
      createConfiguredSource({ VITE_DATA_SOURCE: 'file' }, 'http://localhost/'),
    /needs/
  )
})
test('React data hooks read the committed fixture snapshot without rendering a map', async () => {
  const { source } = harness()
  await source.initialize()
  await source.actions.seek(last.knowledge_sim_ms)
  function Probe() {
    const m = useManifest(),
      cycle = useCycleAt(last.knowledge_sim_ms),
      grid = useArrivalGrid(cycle)
    const events = useEvents(2),
      risk = useZoneRisk(cycle?.cycle_id)
    return createElement(
      'p',
      null,
      `${m!.scenario_id}:${cycle!.knowledge_sim_ms}:${grid!.length}:${events.length}:${risk.length}`
    )
  }
  const html = renderToString(
    createElement(DataSourceProvider, { source }, createElement(Probe))
  )
  assert(
    html.includes(
      `${scenario}:${last.knowledge_sim_ms}:${5 * rawManifest.grid.nx * rawManifest.grid.ny}:${Math.min(2, source.getSnapshot().events.length)}:`
    )
  )
  source.dispose()
})
test('StrictMode-style lease cleanup/reacquire keeps a source alive; a true unmount disposes it', async () => {
  const { source } = harness()
  await source.initialize()
  const release = acquireSource(source)
  release()
  const releaseAgain = acquireSource(source)
  await Promise.resolve()
  assert.notEqual(source.getSnapshot().status, 'disposed')
  releaseAgain()
  await Promise.resolve()
  assert.equal(source.getSnapshot().status, 'disposed')
})

test('branch switch stages independent assets and retains only eligible parent history', async () => {
  const fork = firstForecast.knowledge_sim_ms,
    child = 'b1'
  const branches: Branch[] = [
    {
      scenario_id: scenario,
      branch_id: 'main',
      parent_id: null,
      fork_sim_ms: null,
      reason: 'root',
      created_wall_ms: 0,
    },
    {
      scenario_id: scenario,
      branch_id: child,
      parent_id: 'main',
      fork_sim_ms: fork,
      reason: 'test',
      created_wall_ms: 0,
    },
  ]
  let fetchedChild = 0
  const mainHarness = harness()
  const fetcher = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (!url.includes(`/cache/${scenario}/${child}/`))
      return mainHarness.fetcher(input, init)
    fetchedChild++
    if (url.endsWith('/index.json')) {
      const rows = entries.filter((e) => e.knowledge_sim_ms > fork)
      return Response.json({
        scenario_id: scenario,
        branch_id: child,
        cycles: rows.map((e) => ({
          ...e,
          bundle: e.bundle.replace('/main/', '/b1/'),
        })),
      })
    }
    const originalUrl = url.replace(
      `/cache/${scenario}/${child}/`,
      `/cache/${scenario}/main/`
    )
    const response = await mainHarness.fetcher(originalUrl, init)
    if (!url.endsWith('bundle.json')) return response
    const b = await response.json()
    const swap = (value: any): any => {
      if (Array.isArray(value)) return value.map(swap)
      if (value && typeof value === 'object')
        return Object.fromEntries(
          Object.entries(value).map(([k, v]) => [
            k,
            k === 'branch_id' ? child : swap(v),
          ])
        )
      if (typeof value === 'string')
        return value
          .replace('/main/', '/b1/')
          .replace(`cyc_${scenario}_main_`, `cyc_${scenario}_b1_`)
      return value
    }
    return Response.json(swap(b))
  }) as typeof fetch
  const branchSource = new FileSource({
    assetBase: base.href,
    scenario,
    fetcher,
    branches,
    autoTick: false,
  })
  await branchSource.initialize()
  await branchSource.actions.seek(last.knowledge_sim_ms)
  const parentCycle = branchSource.getSnapshot().cycle!.cycle_id
  await branchSource.actions.switchBranch(child)
  const s = branchSource.getSnapshot()
  assert.equal(s.status, 'ready')
  assert.equal(s.cycle!.branch_id, child)
  assert(
    s.cycles
      .filter((c) => c.branch_id === 'main')
      .every((c) => c.knowledge_sim_ms <= fork)
  )
  assert(fetchedChild > 0)
  await branchSource.actions.switchBranch('main')
  assert.equal(branchSource.getSnapshot().cycle!.cycle_id, parentCycle)
  await assert.rejects(branchSource.actions.switchBranch('unknown'))
  branchSource.dispose()
  mainHarness.source.dispose()
})
test('zero activity limit returns no events and source leases dispose separately on replacement', async () => {
  const a = harness().source,
    b = harness().source
  await a.initialize()
  await b.initialize()
  function Probe() {
    return createElement('p', null, useEvents(0).length)
  }
  assert(
    renderToString(
      createElement(DataSourceProvider, { source: a }, createElement(Probe))
    ).includes('0')
  )
  const releaseA = acquireSource(a)
  releaseA()
  const releaseB = acquireSource(b)
  await Promise.resolve()
  assert.equal(a.getSnapshot().status, 'disposed')
  assert.equal(b.getSnapshot().status, 'ready')
  releaseB()
  await Promise.resolve()
  assert.equal(b.getSnapshot().status, 'disposed')
})
test('missing assets and invalid quantile byte values fail visibly', async () => {
  const b = await readBundle(),
    entry = last,
    manifest = validateManifest(rawManifest, scenario, base)
  const malformed = structuredClone(b)
  malformed.cycle.assets.arrival_q = 'missing.bin'
  validateBundle(malformed, entry, manifest, base)
  const { fetcher, source } = harness()
  const cache = new ArrivalGridCache(fetcher, base)
  await assert.rejects(
    cache.load(malformed.cycle, manifest, new AbortController().signal),
    /HTTP 404/
  )
  source.dispose()
  const altered = harness({
    edit: (relative, bytes) => {
      if (relative === b.cycle.assets.arrival_q) {
        bytes = bytes.slice()
        bytes[0] = 254
      }
      return bytes
    },
  })
  const invalid = new ArrivalGridCache(altered.fetcher, base)
  await assert.rejects(
    invalid.load(b.cycle, manifest, new AbortController().signal),
    /quantile/
  )
  altered.source.dispose()
})

test('epoch changes invalidate metadata as well as binary caches', async () => {
  let version = 'original'
  const { source } = harness({
    edit: (relative, bytes) => {
      if (!relative.endsWith('bundle.json')) return bytes
      const b = JSON.parse(new TextDecoder().decode(bytes))
      if (b.cycle) b.cycle.model_version = version
      return new TextEncoder().encode(JSON.stringify(b))
    },
  })
  await source.initialize()
  await source.actions.seek(firstForecast.knowledge_sim_ms)
  const previous = source.getSnapshot()
  assert.equal(previous.cycle!.model_version, 'original')
  version = 'new-epoch'
  await source.actions.seek(firstForecast.knowledge_sim_ms)
  assert.equal(source.getSnapshot().cycle!.model_version, 'new-epoch')
  assert.equal(previous.cycle!.model_version, 'original')
  source.dispose()
})
test('bad initial index rejects initialization and exposes a recoverable error snapshot', async () => {
  const { source } = harness({
    edit: (relative, bytes) =>
      relative.endsWith('/index.json')
        ? new TextEncoder().encode(
            '{"scenario_id":"wrong","branch_id":"main","cycles":[]}'
          )
        : bytes,
  })
  await assert.rejects(source.initialize(), /identity/)
  assert.equal(source.getSnapshot().status, 'error')
  assert.equal(source.getSnapshot().cycle, null)
  source.dispose()
})
test('a failed playback update is not repeatedly retried by the clock timer', async () => {
  const { source, calls, setWall } = harness({
    edit: (relative, bytes) => {
      if (relative === firstForecast.bundle) {
        const b = JSON.parse(new TextDecoder().decode(bytes))
        b.schema_version = 99
        return new TextEncoder().encode(JSON.stringify(b))
      }
      return bytes
    },
  })
  await source.initialize()
  await source.actions.play()
  setWall(
    1000000 +
      Math.ceil(
        (firstForecast.knowledge_sim_ms - rawManifest.replay_start_sim_ms) / 240
      )
  )
  await source.tick()
  assert.equal(source.getSnapshot().status, 'error')
  const count = calls.length
  await source.tick()
  assert.equal(calls.length, count)
  source.dispose()
})
