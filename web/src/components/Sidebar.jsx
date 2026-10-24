import { burnedAcres, clock, townRisks } from '../lib/fireModel'

export default function Sidebar({ t }) {
  const towns = townRisks(t)
  const acres = burnedAcres(t)

  return (
    <aside className="sidebar">
      <section className="sidebar__section">
        <div className="sidebar__label">Recommendation</div>
        <div className="sidebar__rec-title">Evacuation order for Pine Hollow</div>
        <div className="sidebar__rec-body">
          Fire is moving northeast, faster than this morning&apos;s forecast.
        </div>
      </section>

      <section className="sidebar__section">
        <div className="sidebar__label sidebar__label--spaced">
          Chance fire has reached each town by {clock(t)}
        </div>
        <ul className="town-list">
          {towns.map((town) => (
            <li key={town.name} className="town-list__row">
              <span className="town-list__name">{town.name}</span>
              <span className="town-list__pct" style={{ color: town.color }}>
                {town.pct}
              </span>
            </li>
          ))}
        </ul>
      </section>

      <section className="sidebar__section">
        <div className="sidebar__label">Fire size</div>
        <div className="sidebar__acres">{acres} acres</div>
      </section>

      <section className="sidebar__sources">
        <div className="sidebar__sources-label">Data sources</div>
        <div>GOES-18 · every 5 min</div>
        <div>VIIRS · twice daily</div>
        <div>FireSat · beta</div>
      </section>
    </aside>
  )
}
