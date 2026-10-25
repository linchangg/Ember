import { useEffect, useState } from 'react'
import { leadRows, useReplayJson } from './replayData.js'
import './SlideHowEarly.css'

// A 16:9 slide (1920 x 1080) for the presentation: Emberline's satellite-only order recommendations against the county's
// official orders. Everything is read from the engine's lead_times.json and the zone names; open /slides/how-early.

const fmtHM = (ms) => new Date(ms).toLocaleTimeString('en-US', { timeZone: 'America/Los_Angeles', hour: 'numeric', minute: '2-digit' })
const fmtPT = (ms) => `${new Date(ms).toLocaleString('en-US', { timeZone: 'America/Los_Angeles', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })} PT`
const RUN = 'satellites-only' // the headline run: satellite data only, no field reports

export default function SlideHowEarly() {
  const scn = new URLSearchParams(window.location.search).get('scn') || 'eaton'
  const lead = useReplayJson(`cache/${scn}/lead_times.json`)
  const zones = useReplayJson(`static/${scn}/zones.geojson`)
  const [scale, setScale] = useState(1)
  useEffect(() => {
    // ?fixed=1 pins the slide at 1920 x 1080 (for exporting an image); otherwise it scales to the window
    const fit = () => setScale(new URLSearchParams(window.location.search).get('fixed') ? 1 : Math.min(window.innerWidth / 1920, window.innerHeight / 1080))
    fit()
    window.addEventListener('resize', fit)
    return () => window.removeEventListener('resize', fit)
  }, [])

  const names = new Map((zones?.features ?? []).map((f) => [f.properties.zone_id, f.properties.name]))
  const orders = lead?.branches?.[RUN] ? leadRows(lead, RUN, names).filter((r) => r.kind === 'order' && r.lead != null) : []
  const best = orders.filter((r) => r.lead > 0)
  const earlier = best.length
  const later = orders.filter((r) => r.lead < 0).length
  const max = Math.max(1, ...best.map((r) => r.lead))
  const blind = lead?.branches?.openloop?.west_altadena_0325_summary?.order
  const through = lead?.branches?.[RUN]?.cache_through_sim_ms
  const aar = lead?.sources?.aar

  return (
    <div className="slide-wrap" data-ready={lead && zones ? 'yes' : 'no'}>
      <div className="slide" style={{ transform: `scale(${scale})` }}>
        <header>
          <span className="kicker">EMBERLINE · EATON FIRE REPLAY · JAN 7–8, 2025</span>
          <h1>How early?</h1>
          <p>Emberline's evacuation-order recommendations, using satellite data only, against the county's official orders.</p>
        </header>
        {!lead || !zones ? <div className="loading">Loading the engine's lead times…</div> : (
          <div className="grid">
            <section className="chart">
              <div className="cols"><span>Zone</span><span>Minutes before the official order</span><span>Emberline → County</span></div>
              {best.map((r) => (
                <div className="row" key={r.id}>
                  <b>{r.name}</b>
                  <div className="track"><div className="bar" style={{ width: `${(r.lead / max) * 100}%` }}><em>+{r.lead} min</em></div></div>
                  <span className="times"><i>{fmtHM(r.ours)}</i> → {fmtHM(r.official)}</span>
                </div>
              ))}
            </section>
            <aside>
              <div className="hero">
                <b>{earlier}</b>
                <span>zones where Emberline recommended an order before the county's, from satellite data alone.</span>
              </div>
              {blind ? (
                <div className="card">
                  <h3>Without live satellite data</h3>
                  <p>{blind.n_no_alert === blind.n_zones
                    ? `The same model recommended an order for none of the ${blind.n_zones} west-Altadena zones the county ordered at 3:25 AM.`
                    : `The same model recommended orders for only ${blind.n_zones - blind.n_no_alert} of the ${blind.n_zones} west-Altadena zones the county ordered at 3:25 AM.`}</p>
                </div>
              ) : null}
              <div className="card honest">
                <h3>The whole record</h3>
                <p>Of {orders.length} zones where both issued an order, Emberline was earlier in {earlier} and later in {later}.</p>
              </div>
            </aside>
          </div>
        )}
        <footer>
          Official times: LA County After-Action Review (McChrystal Group){aar ? '' : ''}, corroborated by the Citygate investigation. Emberline times are 5-minute forecast cycles
          {through ? `, compared through ${fmtPT(through)}` : ''}. This compares alert times only, not outcomes.
        </footer>
      </div>
    </div>
  )
}
