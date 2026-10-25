import { useState } from 'react'
import type { PanelModel } from './panelModel.ts'
import {
  displayNumber as number,
  displayProbability as probability,
  displayTime,
} from './panelModel.ts'
import type { ComparisonState } from '../data/comparison.ts'
export default function IncidentPanels({
  model,
  comparison,
  decide,
}: {
  model: PanelModel
  comparison: ComparisonState
  decide: (id: string, state: 'approved' | 'declined', reason: string) => void
}) {
  const [reason, setReason] = useState('')
  const tz = model.manifest?.tz ?? 'UTC',
    time = (ms: number | null | undefined) => displayTime(ms, tz)
  const c = model.cycle
  return (
    <aside
      className="sidebar forecast-panels"
      data-testid="incident-panels"
      data-cycle-id={c?.cycle_id ?? ''}
      data-branch-id={model.branch}
    >
      <section className="sidebar__section">
        <div className="sidebar__label">Published forecast</div>
        <h2 className="sidebar__rec-title">
          {model.manifest?.is_fixture
            ? 'Synthetic replay'
            : 'Historical replay'}{' '}
          · experimental model
        </h2>
        <p className="sidebar__rec-body">
          Scientific calibration, historical skill and performance acceptance
          remain open.
        </p>
        {model.message && <p role="status">{model.message}</p>}
        <dl className="forecast-facts">
          <dt>Active branch</dt>
          <dd>{model.branch}</dd>
          <dt>View</dt>
          <dd>{time(model.viewTime)}</dd>
          <dt>Issued</dt>
          <dd>{time(c?.knowledge_sim_ms)}</dd>
          <dt>Data through</dt>
          <dd>{time(c?.data_through_sim_ms)}</dd>
          <dt>Forecast branch</dt>
          <dd>{c?.branch_id ?? 'Unavailable'}</dd>
          <dt>Model</dt>
          <dd>{c?.model_version ?? 'Unavailable'}</dd>
          <dt>Configuration</dt>
          <dd>{c?.config_hash ?? 'Unavailable'}</dd>
          <dt>Members</dt>
          <dd>{number(c?.n_members, 0)}</dd>
          <dt>ESS</dt>
          <dd>{number(c?.ess, 2)}</dd>
          <dt>Tempering α</dt>
          <dd>{number(c?.tempering_alpha, 4)}</dd>
          <dt>Filter</dt>
          <dd>{c?.filter_mode ?? 'Unavailable'}</dd>
          <dt>Resampled</dt>
          <dd>{c ? String(c.resampled) : 'Unavailable'}</dd>
          <dt>View lead</dt>
          <dd>{c ? `${number(model.lead)} min` : 'Unavailable'}</dd>
        </dl>
      </section>
      <section className="sidebar__section">
        <div className="sidebar__label">Published recommendation</div>
        {model.recommendation ? (
          <>
            <h3>
              {model.briefing?.headline ?? model.recommendation.rule_text}
            </h3>
            <p>{model.recommendation.text}</p>
            <p>
              Act by {time(model.recommendation.act_by_sim_ms)} ·{' '}
              {model.recommendation.state}
            </p>
            {model.briefing && (
              <details>
                <summary>Published briefing · {model.briefing.model}</summary>
                <p>{model.briefing.text}</p>
                <p>
                  Grounded: {String(model.briefing.grounded)} · template
                  fallback: {String(model.briefing.fallback_used)}
                </p>
              </details>
            )}
          </>
        ) : (
          <p>No current-cycle recommendation published.</p>
        )}
      </section>
      <section className="sidebar__section">
        <div className="sidebar__label">
          Zone ranking · {c ? `+${number(model.lead)} min view` : 'unavailable'}
        </div>
        {model.leadIndex < 0 && c && (
          <p>
            Zone probabilities are published only at +30 to +360 minutes in
            30-minute steps. Select a published lead.
          </p>
        )}
        {!model.rows.length ? (
          <p>Zone risk unavailable.</p>
        ) : (
          <ol className="forecast-zones">
            {model.rows.map((row) => (
              <li
                key={row.zone_id}
                data-testid="zone-risk"
                data-cycle-id={row.cycle_id}
                data-zone-id={row.zone_id}
              >
                <strong>
                  {row.rank}. {row.name}
                </strong>
                <span className="forecast-zone-value">
                  {probability(row.probability)} · {row.status}
                </span>
                <dl className="forecast-facts">
                  <dt>ETA q10 / q50 / q90</dt>
                  <dd>
                    {time(row.eta_q10_sim_ms)} / {time(row.eta_q50_sim_ms)} /{' '}
                    {time(row.eta_q90_sim_ms)}
                  </dd>
                  <dt>Act by</dt>
                  <dd>{time(row.act_by_sim_ms)}</dd>
                  <dt>Basis</dt>
                  <dd>{row.act_by_basis}</dd>
                  <dt>Evacuation assumption</dt>
                  <dd>{number(row.evac_min)} min</dd>
                  <dt>Trend</dt>
                  <dd>{row.trend}</dd>
                </dl>
                <p>{row.drivers.join(' · ')}</p>
              </li>
            ))}
          </ol>
        )}
      </section>
      <section className="sidebar__section">
        <div className="sidebar__label">Fire statistics · engine units</div>
        <dl className="forecast-facts">
          <dt>Burned area</dt>
          <dd>{number(c?.burned_ha)} ha</dd>
          <dt>Front length</dt>
          <dd>{number(c?.front_km)} km</dd>
          <dt>Growth</dt>
          <dd>{number(c?.growth_ha_per_h)} ha/h</dd>
          <dt>Heading</dt>
          <dd>
            {c?.head_bearing_deg == null
              ? 'Unavailable'
              : `${number(c.head_bearing_deg)}° true`}
          </dd>
        </dl>
      </section>
      <section className="sidebar__section">
        <div className="sidebar__label">Assimilation branch review</div>
        {comparison.status === 'none' ? (
          <p>
            No parent branch configured. Select a configured report branch for
            matched-issue review.
          </p>
        ) : comparison.status === 'loading' ? (
          <p role="status">
            Loading parent forecast at the same issue and view…
          </p>
        ) : comparison.status !== 'ready' ? (
          <p>
            Comparison unavailable:{' '}
            {comparison.error ?? 'No parent forecast at the exact issue.'}
          </p>
        ) : (
          <div
            data-testid="branch-comparison"
            data-issue-ms={comparison.data!.cycle.knowledge_sim_ms}
          >
            <p>
              Same issue {time(c?.knowledge_sim_ms)} · same view{' '}
              {time(model.viewTime)}. Parent {comparison.parent} q50 contours
              are blue dashed; child contours are brown dashed.
            </p>
            <p>
              Parent burned {number(comparison.data!.cycle.burned_ha)} ha ·
              child burned {number(c?.burned_ha)} ha.
            </p>
            <ul>
              {model.rows.map((row) => (
                <li key={row.zone_id}>
                  {row.name}: parent{' '}
                  {probability(
                    model.leadIndex < 0
                      ? null
                      : comparison.data!.zoneRisk.find(
                          (r) => r.zone_id === row.zone_id
                        )?.p_by_lead[model.leadIndex]
                  )}{' '}
                  · child {probability(row.probability)}
                </li>
              ))}
            </ul>
            <p>
              These are published results. A report is not guaranteed to move
              the boundary.
            </p>
          </div>
        )}
      </section>
      <section className="sidebar__section">
        <div className="sidebar__label">Alerts · local review only</div>
        <p>
          No messages or calls are sent. Prior-cycle alerts retain their
          original issue and identifiers.
        </p>
        <label>
          Decline reason{' '}
          <input
            aria-label="Local decline reason"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            maxLength={300}
          />
        </label>
        {!model.alerts.length ? (
          <p>No eligible alert records.</p>
        ) : (
          model.alerts.map((a) => (
            <article
              className="forecast-alert"
              key={a.alert_id}
              data-alert-id={a.alert_id}
            >
              <h3>
                {a.level} · {a.state}
              </h3>
              <p>{a.text}</p>
              <p>{a.rule_text}</p>
              <p>
                Triggered {time(a.triggered_sim_ms)} · act by{' '}
                {time(a.act_by_sim_ms)}
              </p>
              <p className="forecast-id">
                {a.alert_id} · {a.branch_id} · {a.cycle_id}
              </p>
              {a.branch_id !== model.branch && (
                <p>Inherited parent record. Switch to its branch to review.</p>
              )}
              {a.state === 'proposed' && (
                <div className="forecast-actions">
                  <button
                    disabled={!model.available || a.branch_id !== model.branch}
                    onClick={() => decide(a.alert_id, 'approved', '')}
                  >
                    Approve locally
                  </button>
                  <button
                    disabled={
                      !model.available ||
                      a.branch_id !== model.branch ||
                      !reason.trim()
                    }
                    onClick={() => decide(a.alert_id, 'declined', reason)}
                  >
                    Decline locally
                  </button>
                </div>
              )}
            </article>
          ))
        )}
      </section>
      <section className="sidebar__section">
        <details>
          <summary>Observations · {model.observations.length} eligible</summary>
          <p>
            History through the selected knowledge cutoff. Each record retains
            its original assimilation cycle. Influence is the published
            likelihood spread diagnostic, not a percentage.
          </p>
          {model.observations.map((o) => {
            const ref = (o as typeof o & { source_ref?: unknown }).source_ref
            return (
              <article
                className="forecast-observation"
                key={o.obs_id}
                data-obs-id={o.obs_id}
              >
                <strong>
                  {o.kind}
                  {o.report_kind ? ` · ${o.report_kind}` : ''} ·{' '}
                  {o.hot ? 'hot' : 'clear'} · {o.status}
                </strong>
                <dl className="forecast-facts">
                  <dt>Sensor</dt>
                  <dd>{o.sensor_id}</dd>
                  <dt>Observed</dt>
                  <dd>{time(o.observed_sim_ms)}</dd>
                  <dt>Available</dt>
                  <dd>{time(o.available_sim_ms)}</dd>
                  <dt>Trust</dt>
                  <dd>{number(o.trust, 3)}</dd>
                  <dt>Provenance</dt>
                  <dd>{o.provenance}</dd>
                  <dt>Source reference</dt>
                  <dd>{typeof ref === 'string' ? ref : 'Unavailable'}</dd>
                  <dt>Influence diagnostic</dt>
                  <dd>{number(o.influence, 4)}</dd>
                  <dt>Assimilation cycle</dt>
                  <dd className="forecast-id">{o.cycle_id ?? 'Unavailable'}</dd>
                </dl>
              </article>
            )
          })}
          {!model.observations.length && <p>No eligible observations.</p>}
        </details>
      </section>
      <section className="sidebar__section">
        <details>
          <summary>Report acceptance · {model.reports.length}</summary>
          {model.reports.map((r) => (
            <p key={r.report_id}>
              {r.report_id}: {r.status}
              {r.reject_reason ? ` · ${r.reject_reason}` : ''} · observation{' '}
              {r.observation_id ?? 'Unavailable'}
            </p>
          ))}
          {!model.reports.length && (
            <p>
              No published acceptance summaries. Observation records may still
              include assimilated ground reports.
            </p>
          )}
        </details>
      </section>
      <section className="sidebar__section">
        <details>
          <summary>Activity · {model.events.length} eligible events</summary>
          {model.events.map((e) => (
            <p key={e.event_id}>
              {time(e.sim_ms)} · {e.stage} · {e.actor}: {e.message}
            </p>
          ))}
          {!model.events.length && <p>No published activity events.</p>}
        </details>
      </section>
      <section className="sidebar__sources">
        <div className="sidebar__sources-label">Source provenance</div>
        {(['real', 'simulated', 'derived'] as const).map((kind) => (
          <p key={kind}>
            {kind}:{' '}
            {model.manifest?.honesty[kind]?.join(', ') || 'None declared'}
          </p>
        ))}
        {model.sensors.map((sensor) => (
          <p key={sensor.sensor_id}>
            {sensor.name} · {sensor.status} · {sensor.provenance} · last data{' '}
            {time(sensor.last_data_sim_ms)}
          </p>
        ))}
        <p>Urban spread is a heuristic. Spotting remains disabled.</p>
      </section>
    </aside>
  )
}
