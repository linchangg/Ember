import { useEffect, useRef, useState } from 'react'
import { BRANCHES, useReplayJson } from './replayData.js'
import './ReplayPanels.css'

// Replay-only panels: which precomputed run is shown and the duty-officer call placed on approval.
// Everything shown is read from engine files. (The "How early" comparison now lives on its own slide: SlideHowEarly.jsx.)

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
            <div key={i} className={`rp-line ${l.speaker === 'Ember' ? 'us' : 'them'}`}><b>{l.speaker}</b>{l.text}</div>
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
