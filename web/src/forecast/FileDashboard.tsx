import {
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react'
import { Link } from 'react-router-dom'
import { createConfiguredSource } from '../data/store.ts'
import {
  DataSourceProvider,
  useActions,
  useClock,
  useDataStatus,
  useManifest,
  useMapSelection,
  usePanelSnapshot,
  useSimNow,
  useView,
} from '../data/hooks.ts'
import ForecastMap from './ForecastMap.tsx'
import IncidentPanels from './IncidentPanels.tsx'
import { createTimelineSelection } from './latestSelection.ts'
import { panelModel } from './panelModel.ts'
import { useBranchComparison } from '../data/comparison.ts'
import type { DataSource } from '../data/source.ts'
import type { RasterMode } from './forecastRaster.ts'
import './forecast.css'
import { SpacetimeSource } from '../data/spacetimeSource.ts'
const LEADS = [0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330, 360]
const noopSubscribe = () => () => {}
function configureSource() {
  try {
    return {
      source: createConfiguredSource(import.meta.env, window.location.href),
      error: null,
    }
  } catch (error) {
    return {
      source: null,
      error: error instanceof Error ? error.message : String(error),
    }
  }
}
export default function FileDashboard({ judging = false }: { judging?: boolean }) {
  const [configured, setConfigured] = useState(configureSource)
  const retired = useSyncExternalStore(
    configured.source?.subscribe ?? noopSubscribe,
    () => configured.source?.getSnapshot().status === 'disposed',
    () => false
  )
  useEffect(() => {
    if (!retired) return
    let alive = true
    queueMicrotask(() => {
      if (alive) setConfigured(configureSource())
    })
    return () => {
      alive = false
    }
  }, [retired])
  if (retired)
    return (
      <div className="app" role="status">
        Reconnecting file replay…
      </div>
    )
  if (!configured.source)
    return (
      <div className="app forecast-config-error" role="alert">
        Forecast configuration unavailable: {configured.error}
      </div>
    )
  return (
    <DataSourceProvider source={configured.source}>
      <ConnectedDashboard judging={judging} source={configured.source} />
    </DataSourceProvider>
  )
}
function ConnectedDashboard({
  judging,
  source,
}: {
  judging: boolean
  source: DataSource
}) {
  const hub = source instanceof SpacetimeSource
  const [operatorPin, setOperatorPin] = useState('')
  const panelSnapshot = usePanelSnapshot()
  const panels = panelModel(panelSnapshot)
  const comparison = useBranchComparison(source, panelSnapshot)
  const manifest = useManifest(),
    clock = useClock(),
    simTime = useSimNow(),
    view = useView(),
    actions = useActions()
  const { cycle, assets } = useMapSelection(),
    status = useDataStatus()
  const [mode, setMode] = useState<RasterMode>('probability'),
    [lead, setLead] = useState(hub ? 0 : 120)
  const [actionError, setActionError] = useState<string | null>(null)
  const [actionBusy, setActionBusy] = useState(false)
  const [requestedIssue, setRequestedIssue] = useState<number | null>(null)
  const actionPending = useRef(false)
  const actionsRef = useRef(actions)
  const leadRef = useRef(lead)
  const timelineActive = useRef(0)
  const timeline = useRef<ReturnType<typeof createTimelineSelection> | null>(
    null
  )
  useEffect(() => {
    actionsRef.current = actions
    leadRef.current = lead
    timeline.current ??= createTimelineSelection(
      async (request, stillCurrent) => {
        const release = () =>
          setRequestedIssue((current) =>
            current === request.issue ? null : current
          )
        timelineActive.current += 1
        try {
          await actionsRef.current.pause()
          await actionsRef.current.seek(request.issue)
          if (!stillCurrent()) return
          await actionsRef.current.setScrub(
            request.issue + request.leadMin * 60000
          )
          if (!stillCurrent()) return
          release()
        } catch (error) {
          if (!stillCurrent()) return
          setActionError(
            error instanceof Error ? error.message : String(error)
          )
          release()
        } finally {
          timelineActive.current -= 1
        }
      }
    )
  })
  const format = useMemo(
    () =>
      new Intl.DateTimeFormat('en-US', {
        timeZone: manifest?.tz ?? 'UTC',
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
        timeZoneName: 'short',
      }),
    [manifest?.tz]
  )
  const selectIssue = (issue: number, leadMin = leadRef.current) => {
    setRequestedIssue(issue)
    setActionError(null)
    timeline.current?.request({ issue, leadMin })
  }
  const run = (task: () => Promise<void>) => {
    if (actionPending.current || timelineActive.current > 0) return
    actionPending.current = true
    setActionBusy(true)
    setActionError(null)
    void task()
      .catch((error) =>
        setActionError(error instanceof Error ? error.message : String(error))
      )
      .finally(() => {
        actionPending.current = false
        setActionBusy(false)
      })
  }
  const available =
    status.status === 'ready' &&
    status.forecastStatus === 'available' &&
    !!cycle &&
    !!assets
  const notice =
    status.status === 'error'
      ? status.error
      : status.status === 'loading'
        ? 'Loading forecast assets…'
        : status.forecastStatus === 'not-issued'
          ? 'No forecast yet — select a later replay issue.'
          : status.forecastStatus === 'beyond-horizon'
            ? 'View time is beyond the selected forecast horizon.'
            : hub && clock?.status !== 'ok'
              ? `Runtime ${clock?.status ?? 'connecting'}; showing the last committed forecast at its original issue time.`
              : hub && cycle && simTime - cycle.knowledge_sim_ms > 300000
                ? 'Forecast is behind the shared clock; showing its original issue time.'
                : null
  return (
    <div className="app">
      <header className="header">
        <div className="header__left">
          <Link
            to={judging || manifest?.is_fixture ? '/judge' : '/'}
            className="header__brand"
          >
            Emberline
          </Link>
          <span className="header__incident">
            {manifest?.title ?? 'Loading scenario'}
          </span>
          <span className="header__meta">
            {manifest?.is_fixture
              ? 'Synthetic scenario · cached replay · forecast skill unproven · experimental Rust forecast'
              : 'Historical replay · experimental Rust forecast'}
          </span>
        </div>
        <div className="header__right">
          {judging || manifest?.is_fixture ? (
            <Link to="/" className="header__link">
              World events globe · not this forecast
            </Link>
          ) : null}
          <Link to="/compare" className="header__link">
            Globe bake-off
          </Link>
        </div>
      </header>
      <div className="layout">
        <main className="layout__main">
          {manifest ? (
            <ForecastMap
              manifest={manifest}
              cycle={cycle}
              assets={assets}
              viewTime={view.viewTime}
              mode={mode}
              available={available}
              comparison={
                comparison.status === 'ready' ? comparison.data : null
              }
            />
          ) : (
            <div className="terrain-wrap">
              <p role="status">{status.error ?? 'Loading scenario…'}</p>
            </div>
          )}
          <div className="time-slider forecast-controls">
            <div className="forecast-controls__row">
              {hub && (
                <label>
                  Operator PIN{' '}
                  <input
                    aria-label="Operator PIN"
                    type="password"
                    autoComplete="off"
                    value={operatorPin}
                    onChange={(e) => {
                      setOperatorPin(e.target.value)
                      source.configureOperator(e.target.value, 'Operator')
                    }}
                  />
                </label>
              )}
              {hub && (
                <span>
                  Shared hub · {clock?.status ?? 'connecting'} · approvals
                  update operator state
                </span>
              )}
              <label>
                Branch{' '}
                <select
                  aria-label="Replay branch"
                  value={clock?.active_branch_id ?? 'main'}
                  disabled={!clock || actionBusy || hub}
                  onChange={(event) => {
                    const branch = event.target.value
                    run(async () => {
                      await actions.pause()
                      await actions.switchBranch(branch)
                    })
                  }}
                >
                  {panelSnapshot.branches.map((b) => (
                    <option key={b.branch_id} value={b.branch_id}>
                      {b.branch_id} · {b.reason}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Display{' '}
                <select
                  aria-label="Forecast display"
                  disabled={actionBusy}
                  value={mode}
                  onChange={(event) =>
                    setMode(
                      event.target.value === 'probability'
                        ? 'probability'
                        : (Number(event.target.value) as RasterMode)
                    )
                  }
                >
                  <option value="probability">Probability classes</option>
                  {[10, 30, 50, 70, 90].map((q, i) => (
                    <option key={q} value={i}>
                      q{q} arrival
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Lead{' '}
                <select
                  aria-label="Forecast lead"
                  value={lead}
                  disabled={!manifest}
                  onChange={(event) => {
                    const next = Number(event.target.value)
                    setLead(next)
                    selectIssue(
                      requestedIssue ?? cycle?.knowledge_sim_ms ?? simTime,
                      next
                    )
                  }}
                >
                  {LEADS.filter(
                    (value) => value <= (manifest?.horizon_min ?? 360)
                  ).map((value) => (
                    <option key={value} value={value}>
                      {value === 0 ? 'Replay now' : `+${value} min`}
                    </option>
                  ))}
                </select>
              </label>
              <button
                disabled={!clock || actionBusy}
                onClick={() =>
                  run(async () => {
                    if (clock?.playing) {
                      await actions.pause()
                    } else {
                      setLead(0)
                      await actions.goLive()
                      await actions.play()
                    }
                  })
                }
              >
                {clock?.playing ? 'Pause' : 'Play'}
              </button>
              <button
                disabled={!clock}
                onClick={() => selectIssue(clock!.max_sim_ms)}
              >
                Latest forecast
              </button>
            </div>
            <label htmlFor="forecast-issue">Replay issue time</label>
            <input
              id="forecast-issue"
              aria-label="Replay issue time"
              type="range"
              min={clock?.min_sim_ms ?? 0}
              max={clock?.max_sim_ms ?? 0}
              step={300000}
              value={requestedIssue ?? simTime}
              disabled={!clock}
              onChange={(event) => selectIssue(Number(event.target.value))}
            />
            <div className="time-slider__ticks">
              <span>
                {manifest ? format.format(manifest.replay_start_sim_ms) : '—'}
              </span>
              <span>
                {manifest ? format.format(manifest.replay_end_sim_ms) : '—'}
              </span>
            </div>
            <div className="forecast-times" aria-live="polite">
              <span>
                {status.status === 'loading'
                  ? 'Last committed issue'
                  : 'Issued'}
                : {cycle ? format.format(cycle.knowledge_sim_ms) : 'not issued'}
              </span>
              <span>View: {manifest ? format.format(view.viewTime) : '—'}</span>
            </div>
            <div className="forecast-legend" aria-label="Forecast legend">
              <span>
                <i style={{ background: '#4b3732' }} />
                Modeled burned at issue
              </span>
              {mode === 'probability' ? (
                [
                  ['#fde0c5', '≥10%'],
                  ['#f9b98a', '≥30%'],
                  ['#f07a3a', '≥50%'],
                  ['#e8590c', '≥70%'],
                  ['#d93a2b', '≥90%'],
                ].map(([color, label]) => (
                  <span key={label}>
                    <i style={{ background: color }} />
                    {label}
                  </span>
                ))
              ) : (
                <span>
                  <i style={{ background: '#e8590c' }} />
                  Selected quantile reached
                </span>
              )}
              <span>Solid: published front · dashed: q50 isochrones</span>
            </div>
            {notice && (
              <p role={status.status === 'error' ? 'alert' : 'status'}>
                {notice}
              </p>
            )}
            {actionError && <p role="alert">{actionError}</p>}
          </div>
        </main>
        <IncidentPanels
          shared={hub}
          model={panels}
          comparison={comparison}
          decide={(id, state, reason) =>
            run(() =>
              state === 'approved'
                ? actions.approve(id)
                : actions.decline(id, reason)
            )
          }
        />
      </div>
    </div>
  )
}
