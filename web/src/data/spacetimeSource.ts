/** Live hub source; full resubscription on reconnect, never falls back to mock/file. */
import type {
  Actions,
  DataSource,
  Snapshot,
  GeoCollection,
  BranchComparison,
} from './source.ts'
import { initialSnapshot } from './source.ts'
import { assetRoot, assetUrl, safeId, readResponse } from './assetUrl.ts'
import { validateManifest, validateGeometryCollection } from './validation.ts'
import { ArrivalGridCache } from './arrivalGrid.ts'
import { selectHub } from './hubRows.ts'
import type { HubRows } from './hubRows.ts'
import type { HubTransport } from './hubConnection.ts'
export interface SpacetimeOptions {
  uri: string
  database: string
  scenario: string
  assetBase: string
  pageUrl?: string
  fetcher?: typeof fetch
  now?: () => number
  autoTick?: boolean
  connect?: typeof import('./hubConnection.ts').connectHub
}
export class SpacetimeSource implements DataSource {
  private state = initialSnapshot()
  private listeners = new Set<() => void>()
  private rows: HubRows | null = null
  private readonly base: URL
  private readonly fetcher: typeof fetch
  private readonly grids: ArrivalGridCache
  private readonly now: () => number
  private transport: HubTransport | null = null
  private controller: AbortController | null = null
  private sequence = 0
  private revision = 0
  private selectedKey = ''
  private disposed = false
  private synchronized = false
  private timer: ReturnType<typeof setInterval> | null = null
  private reconnect: ReturnType<typeof setTimeout> | null = null
  private retry = 0
  private init: Promise<void> | null = null
  private scrub: number | null = null
  private pin = ''
  private operator = 'Operator'
  readonly actions: Actions
  private readonly options: SpacetimeOptions
  constructor(options: SpacetimeOptions) {
    this.options = options
    safeId(options.scenario)
    safeId(options.database)
    const uri = new URL(options.uri)
    if (
      !['http:', 'https:', 'ws:', 'wss:'].includes(uri.protocol) ||
      uri.username ||
      uri.password
    )
      throw new Error('Invalid hub URI')
    this.base = assetRoot(options.assetBase, options.pageUrl)
    this.fetcher = options.fetcher ?? fetch
    this.now = options.now ?? Date.now
    this.grids = new ArrivalGridCache(this.fetcher, this.base)
    const call = (name: string, args: Record<string, unknown> = {}) =>
      this.command(name, {
        scenarioId: options.scenario,
        pin: this.pin,
        ...args,
      })
    this.actions = {
      play: () => call('clockPlay'),
      pause: () => call('clockPause'),
      seek: (time) => call('clockSeek', { simMs: time }),
      setSpeed: (speed) => call('clockSetSpeed', { speed }),
      reset: () => call('resetScenario'),
      setPauseOnProposal: (on) => call('setPauseOnProposal', { on }),
      setEvacMin: (zone, minutes) =>
        call('setEvacMin', { zoneId: zone, minutes }),
      setScrub: async (time) => {
        if (
          !Number.isSafeInteger(time) ||
          !this.state.manifest ||
          !this.state.clock ||
          time < this.state.clock.min_sim_ms ||
          time >
            this.state.clock.max_sim_ms +
              this.state.manifest.horizon_min * 60000
        )
          throw new Error('View outside replay/forecast bounds')
        this.scrub = time
        await this.refresh()
      },
      goLive: async () => {
        this.scrub = null
        await this.refresh()
      },
      switchBranch: async (id) => {
        if (id !== this.state.clock?.active_branch_id)
          throw new Error(
            'The shared branch is engine-owned; use the runtime branch/report command'
          )
      },
      approve: (id, text) =>
        this.review(id, 'approveAlert', { editedText: text ?? '' }),
      decline: (id, reason) => {
        if (!reason.trim())
          return Promise.reject(new Error('Decline reason required'))
        return this.review(id, 'declineAlert', { reason })
      },
      injectTapReport: async () => {
        throw new Error(
          'Use the configured report API; a browser cannot submit an engine-owned branch command'
        )
      },
    }
  }
  configureOperator(pin: string, operator: string) {
    this.pin = pin
    this.operator = operator.trim() || 'Operator'
  }
  getSnapshot = () => this.state
  subscribe = (listener: () => void) => {
    if (this.disposed) return () => {}
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }
  private publish(state: Snapshot) {
    if (this.disposed) return
    this.state = state
    for (const fn of this.listeners) fn()
  }
  initialize() {
    if (this.disposed) return Promise.reject(new Error('Source is disposed'))
    return (this.init ??= this.start())
  }
  private async start() {
    try {
      const c = new AbortController()
      this.controller = c
      const manifest = validateManifest(
        await readResponse(
          this.fetcher,
          assetUrl(this.base, `static/${this.options.scenario}/manifest.json`),
          c.signal
        ),
        this.options.scenario,
        this.base
      )
      const zones = validateGeometryCollection(
        await readResponse(
          this.fetcher,
          assetUrl(this.base, manifest.static.zones_url),
          c.signal
        )
      ) as unknown as GeoCollection
      if (this.disposed) return
      this.publish({
        ...this.state,
        status: 'loading',
        manifest,
        zones: zones.features as unknown as Snapshot['zones'],
        error: 'Connecting to hub…',
      })
      await this.connect()
      if (!this.disposed && this.options.autoTick !== false)
        this.timer = setInterval(() => void this.tick(), 100)
    } catch (e) {
      this.fail(e, false)
    }
  }
  private async connect() {
    if (this.disposed) return
    const connect =
      this.options.connect ?? (await import('./hubConnection.ts')).connectHub
    if (this.disposed) return
    this.transport = connect(
      this.options.uri,
      this.options.database,
      this.options.scenario,
      (rows) => {
        this.rows = rows
        this.revision++
        void this.refresh()
      },
      (e) => this.fail(e, true),
      () => {
        this.synchronized = true
        this.retry = 0
      }
    )
  }
  private fail(error: unknown, reconnect: boolean) {
    if (this.disposed) return
    this.controller?.abort()
    this.sequence++
    this.selectedKey = ''
    if (reconnect) this.synchronized = false
    this.publish({
      ...this.state,
      status: 'error',
      error: error instanceof Error ? error.message : String(error),
      pending: null,
      mapAssets: null,
    })
    if (reconnect && !this.reconnect) {
      this.reconnect = setTimeout(
        () => {
          this.reconnect = null
          this.transport?.close()
          this.transport = null
          this.rows = null
          this.grids.clear()
          void this.connect().catch((e) => this.fail(e, true))
        },
        Math.min(30000, 1000 * 2 ** this.retry++)
      )
    }
  }
  private async command(name: string, args: Record<string, unknown>) {
    if (!this.pin)
      throw new Error('Enter the operator PIN before shared hub actions')
    if (!this.synchronized || !this.transport)
      throw new Error('Hub is not synchronized')
    await this.transport.call(name, args)
  }
  private review(id: string, name: string, args: Record<string, unknown>) {
    const alert = this.state.alerts.find((a) => a.alert_id === id)
    if (
      this.state.status !== 'ready' ||
      !alert ||
      alert.branch_id !== this.state.clock?.active_branch_id ||
      alert.state !== 'proposed'
    )
      return Promise.reject(
        new Error('Only an active-branch proposal can be reviewed')
      )
    return this.command(name, {
      alertId: id,
      operator: this.operator,
      pin: this.pin,
      ...args,
    })
  }
  private async refresh() {
    if (
      this.disposed ||
      !this.synchronized ||
      !this.rows ||
      !this.state.manifest
    )
      return
    try {
      const s = selectHub(
        this.rows,
        this.state.manifest,
        this.base,
        this.now(),
        this.scrub
      )
      const key = JSON.stringify([
        this.revision,
        s.clock.epoch,
        s.clock.active_branch_id,
        Math.floor(s.now / 60000),
        Math.floor(s.view / 60000),
        s.cycle?.cycle_id,
        s.view >
          (s.cycle?.knowledge_sim_ms ?? 0) +
            (s.cycle?.horizon_min ?? 0) * 60000,
      ])
      if (key === this.selectedKey && this.state.status === 'ready') {
        this.publish({
          ...this.state,
          clock: s.clock,
          simTime: s.now,
          view: {
            mode: this.scrub === null ? 'live' : 'scrub',
            viewTime: s.view,
          },
        })
        return
      }
      this.selectedKey = key
      const generation = ++this.sequence
      this.controller?.abort()
      const controller = new AbortController()
      this.controller = controller
      if (
        this.state.clock?.epoch !== s.clock.epoch ||
        this.state.clock?.active_branch_id !== s.clock.active_branch_id
      )
        this.grids.clear()
      this.publish({
        ...this.state,
        status: 'loading',
        error: null,
        pending: { simTime: s.now, viewTime: s.view },
      })
      const beyond =
        !!s.cycle &&
        s.view > s.cycle.knowledge_sim_ms + s.cycle.horizon_min * 60000
      const mapAssets =
        s.cycle && !beyond
          ? await this.grids.load(
              s.cycle,
              this.state.manifest,
              controller.signal
            )
          : null
      if (
        this.disposed ||
        generation !== this.sequence ||
        controller.signal.aborted
      )
        return
      this.publish({
        ...this.state,
        status: 'ready',
        error: null,
        pending: null,
        clock: s.clock,
        simTime: s.now,
        view: {
          mode: this.scrub === null ? 'live' : 'scrub',
          viewTime: s.view,
        },
        branches: s.branches,
        evacOverrides: Object.fromEntries(
          (this.rows.zone ?? []).map((z) => [
            String(z.zone_id),
            Number(z.evac_min),
          ])
        ),
        cycles: s.cycles,
        cycle: s.cycle,
        bundle: s.bundle,
        mapAssets,
        forecastStatus: !s.cycle
          ? 'not-issued'
          : beyond
            ? 'beyond-horizon'
            : 'available',
        zoneRisk: s.bundle.zone_risk,
        observations: s.bundle.observations,
        scans: s.bundle.scan_events,
        alerts: s.bundle.alerts,
        briefings: s.bundle.briefings,
        sensors: s.bundle.sensors,
        people: s.bundle.people,
        reports: s.bundle.reports,
        metrics: s.bundle.metrics,
        events: s.bundle.events,
      })
    } catch (e) {
      if (e instanceof Error && e.name === 'AbortError') return
      this.fail(e, false)
    }
  }
  tick = async () => {
    if (this.state.status !== 'error' && this.state.clock?.playing)
      await this.refresh()
  }
  async compareBranch(
    branch: string,
    issue: number,
    view: number,
    signal: AbortSignal
  ): Promise<BranchComparison | null> {
    if (
      !this.rows ||
      !this.state.clock ||
      !this.state.manifest ||
      !this.synchronized
    )
      return null
    const epoch = this.state.clock.epoch,
      revision = this.revision
    const rows = {
      ...this.rows,
      clock: this.rows.clock.map((c) => ({
        ...c,
        active_branch_id: branch,
        anchor_sim_ms: issue,
        playing: false,
      })),
    }
    const s = selectHub(rows, this.state.manifest, this.base, this.now(), view)
    if (
      s.cycle?.knowledge_sim_ms !== issue ||
      s.cycle.branch_id !== branch ||
      view > issue + s.cycle.horizon_min * 60000
    )
      return null
    const mapAssets = await this.grids.load(
      s.cycle,
      this.state.manifest,
      signal
    )
    signal.throwIfAborted()
    if (
      this.disposed ||
      epoch !== this.state.clock?.epoch ||
      revision !== this.revision
    )
      throw new Error('Comparison superseded')
    return { cycle: s.cycle, mapAssets, zoneRisk: s.bundle.zone_risk }
  }
  dispose() {
    if (this.disposed) return
    this.publish({ ...this.state, status: 'disposed' })
    this.disposed = true
    this.sequence++
    this.controller?.abort()
    this.transport?.close()
    if (this.timer) clearInterval(this.timer)
    if (this.reconnect) clearTimeout(this.reconnect)
    this.grids.clear()
    this.pin = ''
    this.listeners.clear()
  }
}
