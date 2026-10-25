import { useState } from 'react'

// Right-hand panels for the Eaton scenario: ranked zones and alerts, straight from the engine's bundle
// (ZoneRisk, Alert, Briefing in spec/00 section 3). Nothing here is computed in the browser.

const fmtHM = (ms) => new Date(ms).toLocaleTimeString('en-US', { timeZone: 'America/Los_Angeles', hour: 'numeric', minute: '2-digit' })
const fmtDay = (ms) => new Date(ms).toLocaleString('en-US', { timeZone: 'America/Los_Angeles', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
const STATUS_RGB = { order: [255, 90, 60], warning: [255, 176, 58], watch: [232, 211, 106], clear: [90, 140, 210] }
const css = (c) => `rgb(${c[0]},${c[1]},${c[2]})`
const STATUS_LABEL = { order: 'Order', warning: 'Warning', watch: 'Watch', clear: 'Clear' }
const TREND = { up: '↑ rising', flat: '→ steady', down: '↓ falling' }
const LEVEL_LABEL = { advisory: 'Advisory', warning: 'Warning', order_recommended: 'Order recommended' }

/** Centroid of a zone's first ring, for flying the map there. */
function zoneCenter(feature) {
  const g = feature.geometry
  const ring = (g.type === 'Polygon' ? g.coordinates : g.coordinates[0])[0]
  return { lon: ring.reduce((s, p) => s + p[0], 0) / ring.length, lat: ring.reduce((s, p) => s + p[1], 0) / ring.length }
}

export function ZonesTab({ scenario, selZone, onSelect }) {
  const [showClear, setShowClear] = useState(false)
  const features = scenario.zones.features
  const byId = new Map(features.map((f) => [f.properties.zone_id, f]))
  const rows = [...scenario.zoneRiskList].sort((a, b) => a.rank - b.rank)
  const active = rows.filter((r) => r.status !== 'clear')
  const clear = rows.filter((r) => r.status === 'clear')
  const counts = Object.fromEntries(['order', 'warning', 'watch'].map((s) => [s, active.filter((r) => r.status === s).length]))
  const now = scenario.viewTime
  const issued = scenario.cycleAt()
  if (!rows.length) return <p className="gn__empty">No zone risk yet. The first forecast has not been issued.</p>
  const Row = ({ r }) => {
    const f = byId.get(r.zone_id)
    const name = f?.properties.name ?? r.zone_id
    const p2h = r.p_by_lead[3]
    const late = r.act_by_sim_ms != null && r.act_by_sim_ms < now
    return (
      <button className={`gn__zone ${selZone === r.zone_id ? 'sel' : ''}`} style={{ '--c': css(STATUS_RGB[r.status]) }} onClick={() => f && onSelect({ id: r.zone_id, ...zoneCenter(f) })}>
        <span className="gn__zone-head">
          <b>{name}</b>
          <i className="gn__chip" style={{ background: css(STATUS_RGB[r.status]) }}>{STATUS_LABEL[r.status]}</i>
        </span>
        <span className="gn__zone-line">
          <span>{Math.round(p2h * 100)}% within 2 h</span>
          <span className={late ? 'late' : ''}>{r.act_by_sim_ms != null ? `${late ? 'act-by passed ' : 'act by '}${fmtHM(r.act_by_sim_ms)}` : 'no act-by'}</span>
        </span>
        <span className="gn__zone-line dim">
          <span>{r.population.toLocaleString()} residents{r.n_critical ? ` · ${r.n_critical} critical` : ''}</span>
          <span>{TREND[r.trend]}</span>
        </span>
        {r.drivers.length ? <span className="gn__zone-drivers">{r.drivers.join(' · ')}</span> : null}
      </button>
    )
  }
  return (
    <div className="gn__zones">
      <div className="gn__zones-sum">
        {counts.order + counts.warning + counts.watch === 0 ? 'No zones at watch or above.' : [counts.order && `${counts.order} order`, counts.warning && `${counts.warning} warning`, counts.watch && `${counts.watch} watch`].filter(Boolean).join(' · ')}
        <span>ranked by engine risk · forecast issued {issued != null ? fmtHM(issued) : '—'} PT</span>
      </div>
      {active.map((r) => <Row key={r.zone_id} r={r} />)}
      {clear.length ? (
        <>
          <button className="gn__more" onClick={() => setShowClear((v) => !v)}>{showClear ? 'Hide' : 'Show'} {clear.length} clear zone{clear.length === 1 ? '' : 's'}</button>
          {showClear ? clear.map((r) => <Row key={r.zone_id} r={r} />) : null}
        </>
      ) : null}
    </div>
  )
}

export function AlertsTab({ scenario, actions, act }) {
  const [open, setOpen] = useState({})
  const names = new Map(scenario.zones.features.map((f) => [f.properties.zone_id, f.properties.name]))
  const briefings = new Map(scenario.briefings.map((b) => [b.briefing_id, b]))
  const alerts = [...scenario.alerts].sort((a, b) => b.triggered_sim_ms - a.triggered_sim_ms)
  if (!alerts.length) return <p className="gn__empty">No alerts yet. Alerts appear when a zone crosses an evacuation rule.</p>
  return (
    <div className="gn__alerts">
      {alerts.map((a) => {
        const b = a.briefing_id ? briefings.get(a.briefing_id) : null
        const proposed = a.state === 'proposed'
        return (
          <div key={a.alert_id} className={`gn__alert ${a.level}`}>
            <div className="gn__alert-head">
              <b>{LEVEL_LABEL[a.level] ?? a.level}</b>
              <i className={`gn__state ${a.state}`}>{a.state}</i>
            </div>
            <div className="gn__alert-zones">{a.zone_ids.map((z) => names.get(z) ?? z).join(', ')}</div>
            <div className="gn__alert-meta">
              Triggered {fmtDay(a.triggered_sim_ms)} PT{a.act_by_sim_ms != null ? ` · act by ${fmtHM(a.act_by_sim_ms)}` : ''}
              <br />Rule {a.rule_id}: {a.rule_text}
            </div>
            <div className="gn__alert-text">{b?.headline ?? ''}</div>
            {b?.bullets?.length ? <ul>{b.bullets.map((x, i) => <li key={i}>{x}</li>)}</ul> : <div className="gn__alert-body">{a.text}</div>}
            <div className="gn__alert-meta">
              {b ? `${b.grounded ? 'Every number checked against the forecast' : 'NOT fully grounded'}${b.fallback_used ? ' · template text used' : ''} · ${a.draft_source === 'claude' ? `written by ${b.model}` : 'template text'}` : `Draft: ${a.draft_source}`}
            </div>
            <button className="gn__link" onClick={() => setOpen((o) => ({ ...o, [a.alert_id]: !o[a.alert_id] }))}>{open[a.alert_id] ? 'Hide' : 'Show'} voice script</button>
            {open[a.alert_id] ? <div className="gn__alert-body dim">{a.voice_script}</div> : null}
            {proposed ? (
              <div className="gn__alert-act">
                <button className="go" onClick={() => act(actions.approve(a.alert_id))}>Approve</button>
                <button onClick={() => { const why = window.prompt('Reason for declining'); if (why) act(actions.decline(a.alert_id, why)) }}>Decline</button>
              </div>
            ) : a.decided_by ? <div className="gn__alert-meta">Decided by {a.decided_by}</div> : null}
          </div>
        )
      })}
    </div>
  )
}
