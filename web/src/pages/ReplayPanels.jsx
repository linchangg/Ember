import { useEffect, useRef, useState } from 'react'
import { BRANCHES, branchLabel, leadRows, useReplayJson } from './replayData.js'
import './ReplayPanels.css'

// Replay-only panels: which precomputed run is shown, the duty-officer call placed on approval,
// and how early Emberline's alerts came compared with the official orders. Everything shown is read from engine files.

const fmtHM = (ms) => new Date(ms).toLocaleTimeString('en-US', { timeZone: 'America/Los_Angeles', hour: 'numeric', minute: '2-digit' })
const fmtPT = (ms) => `${new Date(ms).toLocaleString('en-US', { timeZone: 'America/Los_Angeles', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })} PT`
const LEVEL_LABEL = { advisory: 'Advisory', warning: 'Warning', order_recommended: 'Order recommended' }

/** Compact segmented control; hidden unless more than one branch was precomputed. */
export function BranchSwitcher({ available, active, onSwitch }) {
  if (available.length < 2) return null
  return (
    <div className="rp-branch" role="group" aria-label="Data used by the forecast">
      <span>Data</span>
      {BRANCHES.filter((b) => available.includes(b.id)).map((b) => (
        <button key={b.id} className={b.id === active ? 'on' : ''} aria-pressed={b.id === active} onClick={() => b.id !== active && onSwitch(b.id)}>{b.label}</button>
      ))}
    </div>
  )
}

/** Shows the engine's call for an alert the moment an operator approves it (proposed -> approved). */
export function DutyOfficerCall({ scn, branch, alerts }) {
  const calls = useReplayJson(branch ? `cache/${scn}/${branch}/calls.json` : null)
  const before = useRef(new Map())
  const [call, setCall] = useState(null)
  useEffect(() => {
    const prev = before.current
    before.current = new Map(alerts.map((a) => [a.alert_id, a.state]))
    if (!Array.isArray(calls)) return
    const approved = alerts.find((a) => a.state === 'approved' && prev.get(a.alert_id) === 'proposed')
    const hit = approved && calls.find((c) => c.alert_id === approved.alert_id)
    if (hit) setCall(hit)
  }, [alerts, calls])
  return call ? <CallCard key={call.call_id} call={call} onClose={() => setCall(null)} /> : null
}

const NO_LINES = []
function CallCard({ call, onClose }) {
  const lines = call.transcript ?? NO_LINES
  const [shown, setShown] = useState(0)
  useEffect(() => {
    if (shown >= lines.length) return undefined
    // ring first, then each line after a pause that grows with the previous line's length
    const prev = shown ? lines[shown - 1].text.length : 0
    const id = setTimeout(() => setShown((n) => n + 1), shown ? Math.min(4500, 700 + prev * 12) : 1400)
    return () => clearTimeout(id)
  }, [shown, lines])
  const done = shown >= lines.length
  const passed = call.act_by_sim_ms != null && call.act_by_sim_ms < call.trigger_sim_ms
  const b = call.briefing
  return (
    <div className="rp-call" role="status">
      <button className="rp-x" onClick={onClose} aria-label="Dismiss call">×</button>
      <div className="rp-call-head">
        <i className={done ? 'rp-dot end' : 'rp-dot'} />
        <b>{done ? 'Call to duty officer' : shown ? 'On call with duty officer' : 'Calling duty officer…'}</b>
      </div>
      <div className="rp-call-grid">
        <span>Recipient</span><span>{call.recipient}</span>
        <span>Zones</span><span>{(call.zone_names ?? call.zone_ids).join(', ')}</span>
        <span>Level</span><span>{LEVEL_LABEL[call.level] ?? call.level}</span>
        <span>Act by</span><span className={passed ? 'late' : ''}>{call.act_by_sim_ms != null ? `${fmtHM(call.act_by_sim_ms)} PT${passed ? ' (already passed)' : ''}` : '—'}</span>
        <span>Placed</span><span>{fmtPT(call.trigger_sim_ms)}, on approval</span>
      </div>
      <div className="rp-transcript">
        {lines.slice(0, shown).map((l, i) =>
          l.speaker === 'tool' ? (
            <div key={i} className="rp-line tool">→ {l.text}</div>
          ) : (
            <div key={i} className={`rp-line ${l.speaker === 'Emberline' ? 'us' : 'them'}`}><b>{l.speaker}</b>{l.text}</div>
          )
        )}
        {!done ? <div className="rp-line wait">{shown ? '…' : 'ringing…'}</div> : null}
      </div>
      {done && b ? (
        <div className="rp-brief">
          <b>Briefing sent: {b.headline}</b>
          {b.bullets?.length ? <ul>{b.bullets.map((x, i) => <li key={i}>{x}</li>)}</ul> : <p>{b.text}</p>}
        </div>
      ) : null}
    </div>
  )
}

const leadText = (r) =>
  r.lead == null ? (r.ours == null ? 'no alert' : '—') : r.lead > 0 ? `${r.lead} min earlier` : r.lead < 0 ? `${-r.lead} min later` : 'same minute'
const HONEST = 'satellites-only' // the headline run: satellites only, no responder reports

/** "How early": Emberline's order recommendations vs the official orders, strongest true results first, full record one click away. */
export function LeadTimesTab({ lead, zones }) {
  const [all, setAll] = useState(false)
  const [run, setRun] = useState(HONEST)
  const names = new Map(zones.map((f) => [f.properties.zone_id, f.properties.name]))
  const best = lead.branches?.[HONEST] ? leadRows(lead, HONEST, names).filter((r) => r.kind === 'order' && r.lead > 0) : []
  const blind = lead.branches?.openloop?.west_altadena_0325_summary?.order
  const aar = lead.sources?.aar
  const where = aar?.used?.match(/Appendix \d+(?: Figure \d+)?/)?.[0]
  const rows = leadRows(lead, run, names)
  const groups = [
    ['Emberline earlier', rows.filter((r) => r.lead > 0)],
    ['Same minute', rows.filter((r) => r.lead === 0)],
    ['Emberline later', rows.filter((r) => r.lead < 0)],
    ['No Emberline alert', rows.filter((r) => r.lead == null)],
  ].filter(([, g]) => g.length)
  const extra = (lead.branches?.[run]?.alerted_without_official ?? []).map((id) => names.get(id) ?? id)
  const through = lead.branches?.[run]?.cache_through_sim_ms
  return (
    <div className="rp-lead">
      {best.length ? (
        <>
          <div className="rp-lead-hero">
            <b>{best.length} zone{best.length === 1 ? '' : 's'}</b>
            <span>where Emberline recommended an evacuation order before the official order, using satellite data only (no field reports).</span>
          </div>
          <table className="rp-table">
            <thead><tr><th>Zone</th><th>Official</th><th>Emberline</th><th /></tr></thead>
            <tbody>
              {best.map((r) => (
                <tr key={r.id}>
                  <td>{r.name}</td>
                  <td>{fmtHM(r.official)}</td>
                  <td className="ours">{fmtHM(r.ours)}</td>
                  <td className="gain">+{r.lead} min</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : <p className="gn__empty">No zone where the satellites-only run recommended an order before the official order.</p>}
      {blind ? (
        <p className="rp-contrast">
          {blind.n_no_alert === blind.n_zones
            ? `With no live satellite data, the same model recommended no order for any of the ${blind.n_zones} west-Altadena zones ordered at 3:25 AM.`
            : `With no live satellite data, the same model recommended orders for only ${blind.n_zones - blind.n_no_alert} of the ${blind.n_zones} west-Altadena zones ordered at 3:25 AM.`}
        </p>
      ) : null}
      <button className="gn__more" onClick={() => setAll((v) => !v)}>{all ? 'Hide' : 'Show'} all zones, including later and missed</button>
      {all ? (
        <div className="rp-all">
          <div className="rp-runs">
            {BRANCHES.filter((b) => lead.branches?.[b.id]).map((b) => (
              <button key={b.id} className={run === b.id ? 'on' : ''} onClick={() => setRun(b.id)}>{branchLabel(b.id)}</button>
            ))}
          </div>
          <p className="rp-small">Official order where one was issued, otherwise the official warning (marked W). Compared through {through ? fmtPT(through) : 'the end of the replay'}.</p>
          {groups.map(([title, g]) => (
            <div key={title}>
              <div className="rp-group">{title} ({g.length})</div>
              <table className="rp-table">
                <tbody>
                  {g.map((r) => (
                    <tr key={r.id}>
                      <td>{r.name}{r.kind === 'warn' ? <i className="rp-w">W</i> : null}</td>
                      <td>{fmtHM(r.official)}</td>
                      <td className="ours">{r.ours != null ? fmtHM(r.ours) : '—'}</td>
                      <td className={r.lead > 0 ? 'gain' : r.lead < 0 ? 'loss' : ''}>{leadText(r)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
          {extra.length ? <p className="rp-small">Emberline also alerted {extra.join(', ')}, which had no official warning or order.</p> : null}
          {lead.definition ? <p className="rp-small">{lead.definition}</p> : null}
        </div>
      ) : null}
      {aar ? (
        <p className="rp-src">
          Official times: <a href={aar.url} target="_blank" rel="noreferrer">{aar.title}</a>{where ? `, ${where}` : ''}.
          {lead.sources?.citygate ? ' Corroborated by the Citygate investigation report.' : ''} Times compare alerts only, not outcomes.
        </p>
      ) : null}
    </div>
  )
}
