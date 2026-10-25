import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { createConfiguredSource } from '../data/store.ts'
import {
  DataSourceProvider,
  useActions,
  useClock,
  useDataStatus,
  useManifest,
  useMapSelection,
  useSimNow,
  useView,
} from '../data/hooks.ts'
import ForecastMap from './ForecastMap.tsx'
import type { RasterMode } from './forecastRaster.ts'
import './forecast.css'
const LEADS = [0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330, 360]
export default function FileDashboard() {
  const [configured] = useState(() => {
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
  })
  if (!configured.source)
    return (
      <div className="app forecast-config-error" role="alert">
        Forecast configuration unavailable: {configured.error}
      </div>
    )
  return (
    <DataSourceProvider source={configured.source}>
      <ConnectedDashboard />
    </DataSourceProvider>
  )
}
function ConnectedDashboard() {
  const manifest = useManifest(),
    clock = useClock(),
    simTime = useSimNow(),
    view = useView(),
    actions = useActions()
  const { cycle, assets } = useMapSelection(),
    status = useDataStatus()
  const [mode, setMode] = useState<RasterMode>('probability'),
    [lead, setLead] = useState(120)
  const [actionError, setActionError] = useState<string | null>(null)
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
  const run = (task: () => Promise<void>) => {
    setActionError(null)
    void task().catch((error) =>
      setActionError(error instanceof Error ? error.message : String(error))
    )
  }
  const selectIssue = (issue: number) =>
    run(async () => {
      await actions.pause()
      await actions.seek(issue)
      await actions.setScrub(issue + lead * 60000)
    })
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
            : null
  return (
    <div className="app">
      <header className="header">
        <div className="header__left">
          <Link to="/" className="header__brand">
            Emberline
          </Link>
          <span className="header__incident">
            {manifest?.title ?? 'Loading scenario'}
          </span>
          <span className="header__meta">
            {manifest?.is_fixture ? 'Synthetic replay' : 'Historical replay'} ·
            experimental Rust forecast
          </span>
        </div>
        <div className="header__right">
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
            />
          ) : (
            <div className="terrain-wrap">
              <p role="status">{status.error ?? 'Loading scenario…'}</p>
            </div>
          )}
          <div className="time-slider forecast-controls">
            <div className="forecast-controls__row">
              <label>
                Display{' '}
                <select
                  aria-label="Forecast display"
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
                    run(async () => {
                      await actions.pause()
                      await actions.setScrub(
                        (cycle?.knowledge_sim_ms ?? simTime) + next * 60000
                      )
                    })
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
                disabled={!clock}
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
              value={simTime}
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
                Issued:{' '}
                {cycle ? format.format(cycle.knowledge_sim_ms) : 'not issued'}
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
        <aside className="sidebar">
          <div className="sidebar__label">Map connection</div>
          <h2 className="sidebar__rec-title">Rust forecast layers</h2>
          <p className="sidebar__rec-body">
            This view displays engine forecast files. Zone risks,
            recommendations and alert controls await Phase D.
          </p>
          <p className="sidebar__rec-body">
            Experimental model. Scientific calibration, historical skill and
            performance acceptance remain open.
          </p>
          <p className="sidebar__rec-body">
            {manifest?.is_fixture
              ? 'All scenario inputs shown here are synthetic.'
              : 'Source provenance is declared in the scenario manifest.'}
          </p>
        </aside>
      </div>
    </div>
  )
}
