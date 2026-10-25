import { simNow } from '../contracts.ts'
import type {
  Alert,
  Branch,
  ClockState,
  CycleBundle,
  ZoneFeature,
} from '../contracts.ts'
import type {
  Actions,
  BranchComparison,
  Collections,
  DataSource,
  IndexEntry,
  MapAssets,
  ScenarioManifest,
  Snapshot,
} from './source.ts'
import { initialSnapshot, emptyCollections } from './source.ts'
import { assetRoot, assetUrl, readResponse, safeId } from './assetUrl.ts'
import {
  integer,
  validateBundle,
  validateGeometryCollection,
  validateIndex,
  validateManifest,
} from './validation.ts'
import { ArrivalGridCache } from './arrivalGrid.ts'

export interface FileSourceOptions {
  assetBase: string
  scenario: string
  pageUrl?: string
  fetcher?: typeof fetch
  now?: () => number
  branches?: Branch[]
  autoTick?: boolean
  /** Fetch every bundle of the active branch in the background once its index loads. */
  prefetch?: boolean
}
type Intent = { clock: ClockState; mode: 'live' | 'scrub'; scrub: number }
function freeze<T>(value: T): T {
  if (
    value &&
    typeof value === 'object' &&
    !ArrayBuffer.isView(value) &&
    !Object.isFrozen(value)
  ) {
    for (const child of Object.values(value)) freeze(child)
    Object.freeze(value)
  }
  return value
}
/** Runs `run(0..n-1)` with at most `limit` in flight; stops starting work once `signal` aborts. */
async function pool(
  n: number,
  limit: number,
  run: (k: number) => Promise<void>,
  signal: AbortSignal
) {
  let next = 0
  await Promise.all(
    Array.from({ length: Math.min(limit, n) }, async () => {
      while (next < n) {
        signal.throwIfAborted()
        await run(next++)
      }
    })
  )
}
function latest<T>(rows: T[], key: (row: T) => string): T[] {
  return [...new Map(rows.map((row) => [key(row), row])).values()]
}

export class FileSource implements DataSource {
  private state: Snapshot = freeze(initialSnapshot())
  private listeners = new Set<() => void>()
  private readonly base: URL
  private readonly scenario: string
  private readonly fetcher: typeof fetch
  private readonly now: () => number
  private readonly grids: ArrivalGridCache
  private readonly branches: Branch[]
  private indices = new Map<string, IndexEntry[]>()
  // Bundles are keyed by their whole index entry (content-hashed path), so they survive
  // seeks; a republished index points at new entries and those are fetched afresh.
  private bundles = new Map<string, CycleBundle>()
  private inflight = new Map<string, Promise<CycleBundle>>()
  private branchController = new AbortController()
  private prefetchSignal: AbortSignal | null = null
  private manifest: ScenarioManifest | null = null
  private zones: ZoneFeature[] = []
  private intent: Intent | null = null
  private controller: AbortController | null = null
  private generation = 0
  private disposed = false
  private timer: ReturnType<typeof setInterval> | null = null
  private initializePromise: Promise<void> | null = null
  private committedKey = ''
  private decisions = new Map<string, Alert>()
  private overrides: Record<string, number> = {}
  readonly actions: Actions

  private readonly options: FileSourceOptions
  constructor(options: FileSourceOptions) {
    this.options = options
    this.base = assetRoot(options.assetBase, options.pageUrl)
    this.scenario = safeId(options.scenario)
    this.fetcher = options.fetcher ?? fetch
    this.now = options.now ?? Date.now
    this.grids = new ArrivalGridCache(this.fetcher, this.base)
    this.branches = structuredClone(
      options.branches ?? [
        {
          scenario_id: this.scenario,
          branch_id: 'main',
          parent_id: null,
          fork_sim_ms: null,
          reason: 'File replay',
          created_wall_ms: 0,
        },
      ]
    )
    if (
      new Set(this.branches.map((b) => b.branch_id)).size !==
      this.branches.length
    )
      throw new Error('Duplicate branch descriptor')
    for (const b of this.branches) {
      safeId(b.branch_id)
      if (b.scenario_id !== this.scenario)
        throw new Error('Branch scenario mismatch')
      if (b.parent_id !== null) {
        if (b.fork_sim_ms === null)
          throw new Error('Child branch needs fork time')
        integer(b.fork_sim_ms)
      } else if (b.fork_sim_ms !== null)
        throw new Error('Root branch cannot have fork time')
      this.lineage(b.branch_id) // also rejects cycles and missing parents
    }
    if (!this.branches.some((b) => b.branch_id === 'main'))
      throw new Error('File replay requires a main branch')
    this.actions = {
      play: () => this.changeClock((c) => ({ ...c, playing: true })),
      pause: () => this.changeClock((c) => ({ ...c, playing: false })),
      setSpeed: async (speed) => {
        if (!Number.isFinite(speed) || speed <= 0 || speed > 3600)
          throw new Error('Speed must be in (0,3600]')
        await this.changeClock((c) => ({ ...c, speed }))
      },
      seek: (time) => this.seek(time),
      setScrub: async (time) => {
        const i = this.requireIntent()
        this.checkView(time)
        i.mode = 'scrub'
        i.scrub = time
        await this.refresh()
      },
      goLive: async () => {
        this.requireIntent().mode = 'live'
        await this.refresh()
      },
      reset: async () => {
        this.decisions.clear()
        this.overrides = {}
        await this.seek(this.requireIntent().clock.min_sim_ms, true)
      },
      switchBranch: async (id) => {
        this.lineage(id)
        const i = this.requireIntent()
        this.reanchor()
        i.clock = {
          ...i.clock,
          active_branch_id: id,
          epoch: i.clock.epoch + 1,
          playing: false,
        }
        this.invalidateCaches()
        await this.refresh()
      },
      setPauseOnProposal: (on) =>
        this.changeClock((c) => ({ ...c, pause_on_proposal: on })),
      approve: (id, text) => this.decide(id, 'approved', text),
      decline: (id, reason) => {
        if (!reason.trim())
          return Promise.reject(new Error('Decline requires a reason'))
        return this.decide(id, 'declined')
      },
      setEvacMin: async (zone, minutes) => {
        this.requireIntent()
        if (
          !this.zones.some((z) => z.properties.zone_id === zone) ||
          !Number.isFinite(minutes) ||
          minutes <= 0 ||
          minutes > 1440
        )
          throw new Error('Unknown zone or invalid evacuation duration')
        // Operator override only: never manufacture a recalculated engine risk/act-by.
        this.overrides = { ...this.overrides, [zone]: minutes }
        this.publish({ ...this.state, evacOverrides: this.overrides })
      },
      injectTapReport: async () => {
        throw new Error(
          'FileSource cannot run report assimilation; connect the engine command API in a later phase'
        )
      },
    }
  }
  getSnapshot = () => this.state
  subscribe = (listener: () => void) => {
    // React may briefly resubscribe while a retired source is being replaced.
    // Expose its terminal snapshot without resurrecting it or throwing in commit.
    if (this.disposed) return () => {}
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }
  private publish(state: Snapshot) {
    if (this.disposed) return
    this.state = freeze(state)
    for (const listener of this.listeners) listener()
  }
  private requireIntent() {
    if (this.disposed || !this.intent || !this.manifest)
      throw new Error('Source is not initialized or is disposed')
    return this.intent
  }
  private lineage(id: string): Branch[] {
    const path: Branch[] = []
    const visited = new Set<string>()
    let cursor: string | null = id
    while (cursor !== null) {
      const b = this.branches.find((x) => x.branch_id === cursor)
      if (!b || visited.has(cursor))
        throw new Error('Unknown branch or cyclic lineage')
      visited.add(cursor)
      path.unshift(b)
      cursor = b.parent_id
    }
    for (let k = 1; k < path.length; k++)
      if (
        path[k - 1].fork_sim_ms !== null &&
        path[k].fork_sim_ms! < path[k - 1].fork_sim_ms!
      )
        throw new Error('Nonmonotone branch forks')
    return path
  }
  initialize() {
    if (this.disposed) return Promise.reject(new Error('Source is disposed'))
    return (this.initializePromise ??= this.boot())
  }
  private async boot() {
    const controller = new AbortController()
    this.controller = controller
    const generation = ++this.generation
    this.publish({ ...this.state, status: 'loading' })
    try {
      const m = validateManifest(
        await this.json(
          `static/${this.scenario}/manifest.json`,
          controller.signal
        ),
        this.scenario,
        this.base
      )
      const z = validateGeometryCollection(
        await this.json(m.static.zones_url, controller.signal)
      )
      const zoneIDs = new Set<string>()
      for (const feature of z.features) {
        const p = feature.properties
        if (
          !['Polygon', 'MultiPolygon'].includes(feature.geometry.type) ||
          typeof p.zone_id !== 'string' ||
          typeof p.name !== 'string' ||
          zoneIDs.has(p.zone_id)
        )
          throw new Error('Invalid or duplicate static zone')
        for (const key of [
          'population',
          'housing',
          'n_critical',
          'evac_min_default',
          'egress_roads',
        ])
          if (
            typeof p[key] !== 'number' ||
            !Number.isFinite(p[key]) ||
            p[key] < 0
          )
            throw new Error('Invalid zone properties')
        if (
          ![1, 2, 3].includes(p.tier as number) ||
          typeof p.approximate !== 'boolean'
        )
          throw new Error('Invalid zone tier/provenance')
        zoneIDs.add(p.zone_id)
      }
      if (this.disposed || generation !== this.generation) return
      this.manifest = freeze(m)
      this.zones = freeze(z.features as unknown as ZoneFeature[])
      this.intent = {
        clock: {
          scenario_id: this.scenario,
          mode: 'replay',
          playing: false,
          speed: 240,
          anchor_sim_ms: m.replay_start_sim_ms,
          anchor_wall_ms: this.now(),
          active_branch_id: 'main',
          status: 'ok',
          resume_playing: false,
          pause_on_proposal: true,
          epoch: 0,
          min_sim_ms: m.replay_start_sim_ms,
          max_sim_ms: m.replay_end_sim_ms,
        },
        mode: 'live',
        scrub: m.replay_start_sim_ms,
      }
      await this.refresh()
      if (
        !this.disposed &&
        this.state.status === 'ready' &&
        this.options.autoTick !== false
      )
        this.timer = setInterval(() => {
          void this.tick()
        }, 100)
    } catch (error) {
      if (this.disposed) return
      if (this.manifest) throw error
      if (generation !== this.generation) return
      this.publish({
        ...initialSnapshot(),
        status: 'error',
        error: error instanceof Error ? error.message : String(error),
      })
      throw error
    }
  }
  private json(path: string, signal: AbortSignal) {
    return readResponse(this.fetcher, assetUrl(this.base, path), signal)
  }
  private reanchor() {
    const i = this.requireIntent(),
      wall = this.now()
    i.clock = {
      ...i.clock,
      anchor_sim_ms: Math.floor(simNow(i.clock, wall)),
      anchor_wall_ms: wall,
    }
  }
  private async changeClock(change: (c: ClockState) => ClockState) {
    this.reanchor()
    const i = this.requireIntent()
    i.clock = change(i.clock)
    await this.refresh()
  }
  private checkView(time: number) {
    integer(time)
    const i = this.requireIntent(),
      m = this.manifest!
    if (
      time < i.clock.min_sim_ms ||
      time > i.clock.max_sim_ms + m.horizon_min * 60000
    )
      throw new Error('View outside replay/forecast bounds')
  }
  private async seek(time: number, reset = false) {
    integer(time)
    const i = this.requireIntent()
    if (time < i.clock.min_sim_ms || time > i.clock.max_sim_ms)
      throw new Error('Seek outside replay bounds')
    i.clock = {
      ...i.clock,
      anchor_sim_ms: time,
      anchor_wall_ms: this.now(),
      epoch: i.clock.epoch + 1,
      playing: reset ? false : i.clock.playing,
    }
    i.mode = 'live'
    // Re-read the index so a republished one is picked up; bundles and grids stay cached.
    this.indices.clear()
    await this.refresh()
  }
  private invalidateCaches() {
    this.branchController.abort()
    this.branchController = new AbortController()
    this.grids.clear()
    this.indices.clear()
    this.bundles.clear()
    this.inflight.clear()
    this.committedKey = ''
  }
  /** One shared, validated fetch per index entry; refresh and prefetch both use it. */
  private bundle(entry: IndexEntry): Promise<CycleBundle> {
    const key = JSON.stringify(entry),
      done = this.bundles.get(key)
    if (done) return Promise.resolve(done)
    let p = this.inflight.get(key)
    if (!p) {
      const m = this.manifest!
      p = this.json(entry.bundle, this.branchController.signal)
        .then((raw) => {
          const b = freeze(validateBundle(raw, entry, m, this.base))
          if (this.inflight.get(key) === p) this.bundles.set(key, b)
          return b
        })
        .finally(() => {
          if (this.inflight.get(key) === p) this.inflight.delete(key)
        })
      this.inflight.set(key, p)
    }
    return p
  }
  private async prefetch(all: IndexEntry[], signal: AbortSignal) {
    // ponytail: once per branch; entries from a later republished index load on demand.
    const todo = all.filter((e) => !this.bundles.has(JSON.stringify(e)))
    try {
      await pool(
        todo.length,
        8,
        (k) => this.bundle(todo[k]).then(() => {}),
        signal
      )
    } catch {
      /* a bundle that fails here is refetched, and its error shown, when a refresh needs it */
    }
  }
  private async entries(branch: string, signal: AbortSignal) {
    const path = this.lineage(branch),
      all: IndexEntry[] = []
    for (let k = 0; k < path.length; k++) {
      const b = path[k]
      let entries = this.indices.get(b.branch_id)
      if (!entries) {
        entries = validateIndex(
          await this.json(
            `cache/${this.scenario}/${b.branch_id}/index.json`,
            signal
          ),
          this.scenario,
          b.branch_id,
          this.base
        )
        signal.throwIfAborted()
        if (
          entries.some(
            (e) =>
              e.knowledge_sim_ms < this.manifest!.replay_start_sim_ms ||
              e.knowledge_sim_ms > this.manifest!.replay_end_sim_ms
          )
        )
          throw new Error('Index outside manifest replay bounds')
        this.indices.set(b.branch_id, entries)
      }
      all.push(...entries)
    }
    // Stable sorting gives the active branch preference when a parent and child share an issue.
    return all.sort((a, b) => a.knowledge_sim_ms - b.knowledge_sim_ms)
  }
  private lineageLimits(branch: string) {
    const path = this.lineage(branch)
    return new Map(
      path.map((b, k) => [
        b.branch_id,
        k + 1 < path.length ? path[k + 1].fork_sim_ms! : Infinity,
      ])
    )
  }
  private collect(
    bundles: CycleBundle[],
    limits: Map<string, number>,
    selected: CycleBundle | undefined
  ): Collections {
    const result = emptyCollections()
    for (const b of bundles) {
      const limit = limits.get(b.branch_id) ?? -Infinity
      const within = b.knowledge_sim_ms <= limit
      if (b.cycle && within) result.cycles.push(b.cycle)
      result.observations.push(
        ...b.observations.filter((r) => r.available_sim_ms <= limit)
      )
      result.scans.push(
        ...b.scan_events.filter((r) => r.t_start_sim_ms <= limit)
      )
      if (within) {
        result.briefings.push(...b.briefings)
        result.reports.push(...b.reports)
      }
      result.sensors.push(...b.sensors)
      result.people.push(...b.people)
      result.metrics.push(
        ...b.metrics.filter(
          (r) => r.computed_sim_ms === null || r.computed_sim_ms <= limit
        )
      )
      result.events.push(...b.events.filter((r) => r.sim_ms <= limit))
      // A post-fork parent bundle may carry a later state for an earlier alert.
      // Its trigger time alone cannot date that transition; inherit the last pre-fork state.
      for (const row of (within ? b.alerts : []).filter(
        (r) => r.triggered_sim_ms <= limit
      )) {
        const at = result.alerts.findIndex((a) => a.alert_id === row.alert_id)
        if (at < 0) result.alerts.push(row)
        else if (
          result.alerts[at].state === 'proposed' &&
          row.state === 'superseded'
        )
          result.alerts[at] = row
      }
    }
    result.zoneRisk = selected?.zone_risk ?? []
    result.observations = latest(result.observations, (r) => r.obs_id)
    result.scans = latest(result.scans, (r) => r.scan_id)
    result.briefings = latest(result.briefings, (r) => r.briefing_id)
    result.sensors = latest(result.sensors, (r) => r.sensor_id)
    result.people = latest(result.people, (r) => r.person_id)
    result.reports = latest(result.reports, (r) => r.report_id)
    result.metrics = latest(result.metrics, (r) => `${r.branch_id}:${r.key}`)
    result.events = latest(result.events, (r) => r.event_id)
    result.alerts = result.alerts.map(
      (a) => this.decisions.get(`${a.branch_id}:${a.alert_id}`) ?? a
    )
    return result
  }
  private async refresh() {
    const i = this.requireIntent(),
      m = this.manifest!,
      wall = this.now(),
      simTime = Math.floor(simNow(i.clock, wall))
    if (simTime === i.clock.max_sim_ms && i.clock.playing)
      i.clock = {
        ...i.clock,
        playing: false,
        anchor_sim_ms: simTime,
        anchor_wall_ms: wall,
      }
    const viewTime = i.mode === 'live' ? simTime : i.scrub
    // Abort and generation-check even if the fetch implementation ignores AbortSignal.
    this.controller?.abort()
    const controller = new AbortController()
    this.controller = controller
    const generation = ++this.generation,
      clock = { ...i.clock },
      mode = i.mode
    try {
      const all = await this.entries(clock.active_branch_id, controller.signal)
      if (
        this.disposed ||
        generation !== this.generation ||
        controller.signal.aborted
      )
        return
      const eligible = all.filter(
        (e) => e.knowledge_sim_ms <= Math.min(simTime, viewTime)
      )
      const key = JSON.stringify([
        clock.active_branch_id,
        clock.epoch,
        eligible.map((e) => e.bundle),
      ])
      if (key === this.committedKey && this.state.status === 'ready') {
        const cycle = this.state.cycle
        const beyond =
          !!cycle &&
          viewTime > cycle.knowledge_sim_ms + cycle.horizon_min * 60000
        if (beyond === (this.state.forecastStatus === 'beyond-horizon')) {
          this.publish({
            ...this.state,
            clock,
            simTime,
            view: { mode, viewTime },
            pending: null,
          })
          return
        }
      }
      if (this.disposed || generation !== this.generation) return
      const pending = { simTime, viewTime }
      // Never leave a later forecast on screen while an earlier time loads.
      const shown = this.state.cycle,
        hide = !!shown && shown.knowledge_sim_ms > Math.min(simTime, viewTime)
      let announced = false
      if (eligible.some((e) => !this.bundles.has(JSON.stringify(e)))) {
        announced = true
        this.publish(
          hide
            ? {
                ...initialSnapshot(),
                manifest: m,
                clock,
                simTime,
                view: { mode, viewTime },
                branches: this.branches,
                zones: this.zones,
                evacOverrides: this.overrides,
                status: 'loading',
                pending,
              }
            : { ...this.state, status: 'loading', error: null, pending }
        )
      }
      const staged: CycleBundle[] = new Array(eligible.length)
      await pool(
        eligible.length,
        8,
        async (k) => {
          staged[k] = await this.bundle(eligible[k])
        },
        controller.signal
      )
      if (
        this.disposed ||
        generation !== this.generation ||
        controller.signal.aborted
      )
        return
      const limits = this.lineageLimits(clock.active_branch_id)
      const bundle =
          staged.findLast(
            (b) => b.knowledge_sim_ms <= (limits.get(b.branch_id) ?? -Infinity)
          ) ?? null,
        cycle = bundle?.cycle ?? null
      const beyond =
        !!cycle && viewTime > cycle.knowledge_sim_ms + cycle.horizon_min * 60000
      const collections = this.collect(staged, limits, bundle ?? undefined)
      const newProposal = collections.alerts.some(
        (a) =>
          a.state === 'proposed' &&
          !this.state.alerts.some((old) => old.alert_id === a.alert_id)
      )
      const snapshot = (mapAssets: MapAssets | null): Snapshot => ({
        ...collections,
        status: 'ready',
        error: null,
        manifest: m,
        clock,
        simTime,
        view: { mode, viewTime },
        pending: null,
        forecastStatus: !cycle
          ? 'not-issued'
          : beyond
            ? 'beyond-horizon'
            : 'available',
        bundle,
        cycle,
        mapAssets,
        branches: this.branches,
        zones: this.zones,
        evacOverrides: this.overrides,
      })
      let mapAssets: MapAssets | null = null
      if (cycle && !beyond) {
        if (!this.grids.has(cycle, m)) {
          // A later forecast must not stay up: show the new issue's data while its grid loads.
          if (hide)
            this.publish({ ...snapshot(null), status: 'loading', pending })
          else if (!announced)
            this.publish({
              ...this.state,
              status: 'loading',
              error: null,
              pending,
            })
        }
        mapAssets = await this.grids.load(cycle, m, controller.signal)
        if (
          this.disposed ||
          generation !== this.generation ||
          controller.signal.aborted
        )
          return
      }
      if (clock.pause_on_proposal && newProposal && clock.playing) {
        clock.playing = false
        clock.anchor_sim_ms = simTime
        clock.anchor_wall_ms = wall
        i.clock = clock
      }
      this.committedKey = key
      this.publish(snapshot(mapAssets))
      const signal = this.branchController.signal
      if (this.options.prefetch !== false && this.prefetchSignal !== signal) {
        this.prefetchSignal = signal
        void this.prefetch(all, signal)
      }
    } catch (error) {
      if (
        this.disposed ||
        generation !== this.generation ||
        controller.signal.aborted
      )
        return
      this.committedKey = ''
      this.publish({
        ...initialSnapshot(),
        manifest: m,
        zones: this.zones,
        branches: this.branches,
        clock,
        simTime,
        view: { mode, viewTime },
        status: 'error',
        error: error instanceof Error ? error.message : String(error),
        evacOverrides: this.overrides,
      })
      throw error
    }
  }
  /** Read-only exact-issue comparison. Never changes the parent or selected snapshot. */
  compareBranch = async (
    branch: string,
    issue: number,
    view: number,
    signal: AbortSignal
  ): Promise<BranchComparison | null> => {
    this.requireIntent()
    signal.throwIfAborted()
    this.lineage(branch)
    integer(issue)
    integer(view)
    if (issue > this.state.simTime || view < issue)
      throw new Error('Comparison exceeds available knowledge')
    const epoch = this.state.clock!.epoch
    const entries = await this.entries(branch, signal)
    const entry = entries.findLast(
      (e) => e.branch_id === branch && e.knowledge_sim_ms === issue
    )
    if (!entry) return null
    const bundle = validateBundle(
      await this.json(entry.bundle, signal),
      entry,
      this.manifest!,
      this.base
    )
    const cycle = bundle.cycle
    if (!cycle || view > issue + cycle.horizon_min * 60000) return null
    const mapAssets = await this.grids.load(cycle, this.manifest!, signal)
    signal.throwIfAborted()
    if (this.disposed || epoch !== this.state.clock?.epoch)
      throw new Error('Comparison superseded by replay change')
    return { cycle, mapAssets, zoneRisk: bundle.zone_risk }
  }
  tick = async () => {
    if (
      this.disposed ||
      !this.intent ||
      !this.intent.clock.playing ||
      this.state.status !== 'ready'
    )
      return
    try {
      await this.refresh()
    } catch {
      /* refresh exposes failure; timer must not create an unhandled rejection */
    }
  }
  private async decide(
    id: string,
    state: 'approved' | 'declined',
    text?: string
  ) {
    this.requireIntent()
    const alert = this.state.alerts.find((a) => a.alert_id === id)
    if (alert && alert.branch_id !== this.state.clock?.active_branch_id)
      throw new Error(
        'Switch to the alert branch before reviewing its proposal'
      )
    if (!alert || alert.state !== 'proposed')
      throw new Error('Only a visible proposal can be decided')
    if (text !== undefined && (!text.trim() || text.length > 300))
      throw new Error('Alert text must be 1..300 characters')
    const updated = {
      ...alert,
      state,
      text: text ?? alert.text,
      decided_by: 'Offline operator',
      decided_wall_ms: this.now(),
    }
    this.decisions.set(`${alert.branch_id}:${alert.alert_id}`, updated)
    this.publish({
      ...this.state,
      alerts: this.state.alerts.map((a) => (a.alert_id === id ? updated : a)),
    })
  }
  dispose() {
    if (this.disposed) return
    this.controller?.abort()
    this.branchController.abort()
    ++this.generation
    if (this.timer) clearInterval(this.timer)
    this.grids.clear()
    this.indices.clear()
    this.bundles.clear()
    this.inflight.clear()
    this.decisions.clear()
    this.state = freeze({ ...initialSnapshot(), status: 'disposed' })
    for (const listener of this.listeners) listener()
    this.listeners.clear()
    this.disposed = true
  }
}
