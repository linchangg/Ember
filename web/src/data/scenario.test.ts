import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { openScenario } from './scenario.ts'
import { projectGrid } from '../forecast/reprojection.ts'
const root =
  process.env.EMBERLINE_FIXTURE_DIR ??
  fileURLToPath(new URL('../../../fixtures/', import.meta.url))
const scenario = process.env.EMBERLINE_SCENARIO_ID ?? 'fixture'
const base = 'http://fixture.test/'
const fetcher: typeof fetch = async (input) => {
  try {
    return new Response(
      await readFile(path.join(root, new URL(String(input)).pathname))
    )
  } catch {
    return new Response('missing', { status: 404 })
  }
}
async function ready(
  sc: Awaited<ReturnType<typeof openScenario>>,
  now: number,
  view = now,
  branch = 'main',
  epoch = 0
) {
  for (let i = 0; i < 200; i++) {
    const state = sc.select(now, view, branch, epoch)
    if (state.status !== 'loading') return state
    await new Promise((resolve) => setTimeout(resolve, 2))
  }
  throw new Error('Selection did not settle')
}
const open = (f = fetcher) => openScenario(scenario, { base, fetcher: f })
test('Linda scenario path accepts every canonical/native entry including pre-ignition, with one atomic issue', async () => {
  const sc = await open()
  try {
    let forecast = 0,
      pre = 0
    for (const entry of sc.cycles) {
      const state = await ready(sc, entry.knowledge_sim_ms)
      if (state.status === 'not-issued') {
        pre++
        assert.equal(state.entry, null)
        continue
      }
      assert.equal(state.status, 'ready', state.error ?? '')
      forecast++
      assert.equal(state.entry!.bundle.knowledge_sim_ms, entry.knowledge_sim_ms)
      assert.equal(
        state.entry!.arrival!.length,
        5 * sc.manifest.grid.nx * sc.manifest.grid.ny
      )
      assert(
        state.entry!.bundle.observations.every(
          (o) => o.available_sim_ms <= entry.knowledge_sim_ms
        )
      )
      assert(
        [...state.entry!.zoneRisk.values()].every(
          (r) => r.cycle_id === state.entry!.bundle.cycle!.cycle_id
        )
      )
    }
    assert.equal(forecast, 36)
    assert.equal(pre, 3)
  } finally {
    sc.dispose()
  }
})
test('external clock selection never exposes future issue, respects horizon and caches masks', async () => {
  const sc = await open()
  try {
    const now = sc.cycles[10].knowledge_sim_ms
    const state = await ready(sc, now, now + 120 * 60000)
    assert.equal(state.issue, now)
    for (const q of ['probability', 0, 1, 2, 3, 4] as const) {
      const a = sc.raster(state.entry!, now + 120 * 60000, q)
      assert.strictEqual(
        a,
        sc.raster(state.entry!, now + 120 * 60000 + 1000, q)
      )
    }
    assert.equal(sc.select(now, now + 361 * 60000).status, 'beyond-horizon')
    const past = await ready(sc, now, sc.cycles[0].knowledge_sim_ms)
    assert.equal(past.status, 'not-issued')
    assert.equal(past.entry, null)
    assert.equal(sc.select(now, now, 'unconfigured-child', 1).status, 'error')
    assert.equal(sc.getSnapshot().entry, null)
  } finally {
    sc.dispose()
  }
})
test('asset failure clears selection, does not auto-retry every frame, and explicit retry recovers', async () => {
  let fail = false,
    requests = 0
  const sc = await open(async (input, init) => {
    requests++
    if (fail && String(input).endsWith('arrival_q.bin'))
      return new Response('missing', { status: 404 })
    return fetcher(input, init)
  })
  try {
    const a = sc.cycles[8].knowledge_sim_ms,
      b = sc.cycles[9].knowledge_sim_ms
    assert.equal((await ready(sc, a)).status, 'ready')
    fail = true
    const failure = await ready(sc, b)
    assert.equal(failure.status, 'error')
    assert.match(failure.error!, /404/)
    assert.equal(failure.entry, null)
    const count = requests
    for (let i = 0; i < 10; i++) sc.select(b)
    assert.equal(requests, count)
    fail = false
    sc.retry()
    assert.equal((await ready(sc, b)).status, 'ready')
  } finally {
    sc.dispose()
  }
})
test('late old bundle responses cannot replace a newer seek; disposal prevents resurrection', async () => {
  let unblock!: () => void
  let held = false
  const delayed = new Promise<void>((resolve) => {
    unblock = resolve
  })
  const sc = await open(async (input, init) => {
    if (held && String(input).endsWith('bundle.json')) {
      held = false
      await delayed
    }
    return fetcher(input, init) // deliberately ignores AbortSignal
  })
  const a = sc.cycles[8].knowledge_sim_ms,
    b = sc.cycles[9].knowledge_sim_ms
  try {
    held = true
    assert.equal(sc.select(a).status, 'loading')
    assert.equal((await ready(sc, b)).issue, b)
    unblock()
    await new Promise((resolve) => setTimeout(resolve, 15))
    assert.equal(sc.select(b).issue, b)
    assert.equal(sc.select(b).entry!.bundle.knowledge_sim_ms, b)
    held = true
    sc.select(sc.cycles[10].knowledge_sim_ms)
    sc.dispose()
    assert.equal(sc.getSnapshot().status, 'disposed')
    assert.equal(sc.select(a).entry, null)
  } finally {
    unblock()
    sc.dispose()
  }
})
test('epoch changes invalidate cached files; corrupt bytes fail rather than restoring old output', async () => {
  let corrupt = false
  const sc = await open(async (input, init) => {
    if (corrupt && String(input).endsWith('arrival_q.bin'))
      return new Response(new Uint8Array(5))
    return fetcher(input, init)
  })
  try {
    const now = sc.cycles[10].knowledge_sim_ms
    assert.equal((await ready(sc, now)).status, 'ready')
    corrupt = true
    const failed = await ready(sc, now, now, 'main', 1)
    assert.equal(failed.status, 'error')
    assert.match(failed.error!, /length/)
    assert.equal(failed.entry, null)
  } finally {
    sc.dispose()
  }
})
test('missing index fails explicitly and startup can be cancelled', async () => {
  await assert.rejects(
    open(async (input, init) =>
      String(input).endsWith('index.json')
        ? new Response('missing', { status: 404 })
        : fetcher(input, init)
    ),
    /404/
  )
  const controller = new AbortController()
  controller.abort()
  await assert.rejects(
    openScenario(scenario, { base, fetcher, signal: controller.signal }),
    /abort/i
  )
})
test('globe cell geometry agrees with independent PROJ reference, including shared outer corners', async () => {
  const golden = JSON.parse(
    await readFile(
      new URL('../forecast/georeference.reference.json', import.meta.url),
      'utf8'
    )
  )
  for (const item of golden.cases) {
    const { centers, corners } = projectGrid(item.grid)
    for (const sample of item.samples) {
      const i = (sample.row * item.grid.nx + sample.col) * 2
      assert(Math.abs(centers[i] - sample.lon) < 1e-7)
      assert(Math.abs(centers[i + 1] - sample.lat) < 1e-7)
    }
    assert(Math.abs(corners[0] - item.grid.corners.tl[0]) < 1e-7)
    assert(
      Math.abs(corners[corners.length - 1] - item.grid.corners.br[1]) < 1e-5
    )
  }
})

test('active Linda loader rejects future evidence and malformed map metadata before publication', async () => {
  await assert.rejects(
    open(async (input, init) => {
      const response = await fetcher(input, init)
      if (!String(input).endsWith('manifest.json')) return response
      const m = await response.json()
      m.initial_view.longitude = null
      return Response.json(m)
    }),
    /coordinate|number/i
  )
  const sc = await open(async (input, init) => {
    const response = await fetcher(input, init)
    if (!String(input).endsWith('bundle.json')) return response
    const b = await response.json()
    b.observations[0].available_sim_ms = b.knowledge_sim_ms + 1
    return Response.json(b)
  })
  try {
    const s = await ready(sc, sc.cycles.at(-1)!.knowledge_sim_ms)
    assert.equal(s.status, 'error')
    assert.equal(s.entry, null)
    assert.match(s.error!, /future|available|causal/i)
  } finally {
    sc.dispose()
  }
  const malformed = await open(async (input, init) => {
    const response = await fetcher(input, init)
    if (!String(input).endsWith('perimeter.geojson')) return response
    const geo = await response.json()
    geo.features[0].geometry = { type: 'Point', coordinates: [-118, 34] }
    return Response.json(geo)
  })
  try {
    const failed = await ready(
      malformed,
      malformed.cycles.at(-1)!.knowledge_sim_ms
    )
    assert.equal(failed.status, 'error')
    assert.equal(failed.entry, null)
    assert.match(failed.error!, /geometry types/)
  } finally {
    malformed.dispose()
  }
})
test('complete-entry LRU retains at most four issues and refetches an evicted issue', async () => {
  const counts = new Map<string, number>()
  const sc = await open(async (input, init) => {
    const url = String(input)
    counts.set(url, (counts.get(url) ?? 0) + 1)
    return fetcher(input, init)
  })
  try {
    const entries = sc.cycles.slice(5, 10)
    for (const e of entries)
      assert.equal((await ready(sc, e.knowledge_sim_ms)).status, 'ready')
    const url = new URL(entries[0].bundle, base).href
    assert.equal(counts.get(url), 1)
    await ready(sc, entries[0].knowledge_sim_ms)
    assert.equal(counts.get(url), 2)
  } finally {
    sc.dispose()
  }
})
