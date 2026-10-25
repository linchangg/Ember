import { LEADS_MIN } from '../contracts.ts'
import type { Snapshot } from '../data/source.ts'
/** View-only selection. Never interpolates probabilities or recalculates engine decisions. */
export function panelModel(snapshot: Snapshot) {
  const { status, cycle, forecastStatus } = snapshot
  const available =
    status === 'ready' &&
    forecastStatus === 'available' &&
    !!cycle &&
    !!snapshot.mapAssets
  const lead = cycle
    ? (snapshot.view.viewTime - cycle.knowledge_sim_ms) / 60000
    : null
  const leadIndex =
    lead === null ? -1 : LEADS_MIN.findIndex((value) => value === lead)
  const cutoff = Math.min(snapshot.simTime, snapshot.view.viewTime)
  const rows = available
    ? snapshot.zoneRisk
        .filter(
          (r) =>
            r.cycle_id === cycle.cycle_id && r.branch_id === cycle.branch_id
        )
        .map((row) => ({
          ...row,
          name:
            snapshot.zones.find((z) => z.properties.zone_id === row.zone_id)
              ?.properties.name ?? row.zone_id,
          probability: leadIndex < 0 ? null : row.p_by_lead[leadIndex],
        }))
        .sort((a, b) => a.rank - b.rank || a.zone_id.localeCompare(b.zone_id))
    : []
  const historical = status === 'ready'
  const alerts = historical
    ? snapshot.alerts
        .filter((a) => a.triggered_sim_ms <= cutoff)
        .slice()
        .sort(
          (a, b) =>
            b.triggered_sim_ms - a.triggered_sim_ms ||
            a.alert_id.localeCompare(b.alert_id)
        )
    : []
  const observations = historical
    ? snapshot.observations
        .filter((o) => o.available_sim_ms <= cutoff)
        .slice()
        .sort(
          (a, b) =>
            b.available_sim_ms - a.available_sim_ms ||
            a.obs_id.localeCompare(b.obs_id)
        )
    : []
  const events = historical
    ? snapshot.events
        .filter((e) => e.sim_ms <= cutoff)
        .slice()
        .sort(
          (a, b) => b.sim_ms - a.sim_ms || a.event_id.localeCompare(b.event_id)
        )
    : []
  const recommendation = available
    ? (alerts.find(
        (a) =>
          a.cycle_id === cycle?.cycle_id &&
          ['proposed', 'approved'].includes(a.state)
      ) ?? null)
    : null
  const briefing = recommendation
    ? (snapshot.briefings.find(
        (b) =>
          b.briefing_id === recommendation.briefing_id &&
          b.cycle_id === recommendation.cycle_id &&
          b.alert_id === recommendation.alert_id
      ) ?? null)
    : null
  const message =
    status === 'error'
      ? `Unavailable: ${snapshot.error}`
      : status === 'loading'
        ? 'Loading forecast — panels and map are updating.'
        : forecastStatus === 'not-issued'
          ? 'No forecast yet.'
          : forecastStatus === 'beyond-horizon'
            ? 'View is beyond the forecast horizon.'
            : status !== 'ready'
              ? 'Forecast unavailable.'
              : null
  return {
    available,
    cycle: available ? cycle : null,
    lead,
    leadIndex,
    rows,
    alerts,
    observations,
    events,
    recommendation,
    briefing,
    message,
    branch: snapshot.clock?.active_branch_id ?? 'main',
    epoch: snapshot.clock?.epoch ?? 0,
    viewTime: snapshot.view.viewTime,
    manifest: snapshot.manifest,
    reports: historical ? snapshot.reports : [],
    sensors: historical ? snapshot.sensors : [],
  }
}
export type PanelModel = ReturnType<typeof panelModel>
export const displayNumber = (value: number | null | undefined, digits = 1) =>
  value == null || !Number.isFinite(value)
    ? 'Unavailable'
    : value.toLocaleString('en-US', { maximumFractionDigits: digits })
export const displayProbability = (value: number | null | undefined) =>
  value == null ? 'Unavailable' : `${displayNumber(value * 100, 1)}%`
export function displayTime(value: number | null | undefined, tz = 'UTC') {
  return value == null
    ? 'Unavailable'
    : new Intl.DateTimeFormat('en-US', {
        timeZone: tz,
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
        timeZoneName: 'short',
      }).format(value)
}
