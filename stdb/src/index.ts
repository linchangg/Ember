// Emberline hub module. spec/04_hub.md §2; types in spec/00_contracts.md §3.
// Conventions: times are f64 epoch ms, counts u32/i32, nested data as *_json strings,
// nullables as t.option. Identities are stored as hex strings (04 §2.2 fallback).
import { schema, table, t, SenderError, type InferSchema, type ReducerCtx } from 'spacetimedb/server';

const MAX_EVENTS = 300;

// ---------------------------------------------------------------- tables (04 §2.1)

const config = table(
  { name: 'config' }, // private
  {
    id: t.string().primaryKey(),
    engine_identity: t.string(),
    service_identity: t.string(),
    operator_pin: t.string(),
  }
);

const scenario = table(
  { name: 'scenario', public: true },
  {
    scenario_id: t.string().primaryKey(),
    title: t.string(),
    tz: t.string(),
    manifest_json: t.string(),
    config_hash: t.option(t.string()),
    replay_start_sim_ms: t.f64(),
    replay_end_sim_ms: t.f64(),
  }
);

const clock = table(
  { name: 'clock', public: true },
  {
    scenario_id: t.string().primaryKey(),
    mode: t.string(),
    playing: t.bool(),
    speed: t.f64(),
    anchor_sim_ms: t.f64(),
    anchor_wall_ms: t.f64(),
    active_branch_id: t.string(),
    status: t.string(),
    resume_playing: t.bool(),
    pause_on_proposal: t.bool(),
    epoch: t.u32(),
    min_sim_ms: t.f64(),
    max_sim_ms: t.f64(),
  }
);

const branch = table(
  { name: 'branch', public: true },
  {
    branch_id: t.string().primaryKey(),
    scenario_id: t.string().index('btree'),
    parent_id: t.option(t.string()),
    fork_sim_ms: t.option(t.f64()),
    reason: t.string(),
    created_wall_ms: t.f64(),
  }
);

const sensor = table(
  { name: 'sensor', public: true },
  {
    sensor_id: t.string().primaryKey(),
    scenario_id: t.string().index('btree'),
    kind: t.string(),
    name: t.string(),
    platform: t.string(),
    provenance: t.string(),
    enabled: t.bool(),
    status: t.string(),
    last_data_sim_ms: t.option(t.f64()),
    latency_s: t.option(t.f64()),
    lon: t.option(t.f64()),
    lat: t.option(t.f64()),
    heading_deg: t.option(t.f64()),
    hfov_deg: t.option(t.f64()),
    pixel_m: t.option(t.f64()),
  }
);

const scan_event = table(
  { name: 'scan_event', public: true },
  {
    scan_id: t.string().primaryKey(),
    scenario_id: t.string().index('btree'),
    branch_id: t.string(),
    sensor_id: t.string(),
    mode: t.string(),
    t_start_sim_ms: t.f64(),
    t_end_sim_ms: t.f64(),
    available_sim_ms: t.f64(),
    n_pixels_aoi: t.u32(),
    n_hot: t.u32(),
    n_clear: t.u32(),
    assimilated: t.bool(),
    pixels_path: t.option(t.string()),
    provenance: t.string(),
  }
);

const observation = table(
  { name: 'observation', public: true },
  {
    obs_id: t.string().primaryKey(),
    scenario_id: t.string().index('btree'),
    branch_id: t.string(),
    sensor_id: t.string(),
    kind: t.string(),
    observed_sim_ms: t.f64(),
    available_sim_ms: t.f64(),
    geometry_json: t.string(),
    hot: t.bool(),
    footprint_key: t.option(t.string()),
    fdc_class: t.option(t.i32()),
    report_kind: t.option(t.string()),
    confidence: t.f64(),
    frp_mw: t.option(t.f64()),
    trust: t.f64(),
    provenance: t.string(),
    source_ref: t.option(t.string()),
    status: t.string(),
    cycle_id: t.option(t.string()),
    influence: t.option(t.f64()),
  }
);

const cycle = table(
  { name: 'cycle', public: true },
  {
    cycle_id: t.string().primaryKey(),
    scenario_id: t.string().index('btree'),
    branch_id: t.string(),
    knowledge_sim_ms: t.f64(),
    data_through_sim_ms: t.f64(),
    created_wall_ms: t.f64(),
    source: t.string(),
    filter_mode: t.string(),
    n_members: t.u32(),
    ess: t.f64(),
    tempering_alpha: t.f64(),
    resampled: t.bool(),
    n_obs_by_kind_json: t.string(),
    horizon_min: t.u32(),
    step_min: t.u32(),
    assets_json: t.string(),
    danger_json: t.string(),
    burned_ha: t.f64(),
    front_km: t.f64(),
    growth_ha_per_h: t.f64(),
    head_bearing_deg: t.option(t.f64()),
    params_summary_json: t.string(),
    model_version: t.string(),
    config_hash: t.option(t.string()),
    duration_ms: t.u32(),
  }
);

const cycle_head = table(
  { name: 'cycle_head', public: true },
  {
    id: t.string().primaryKey(), // {scenario}:{branch}
    scenario_id: t.string().index('btree'),
    branch_id: t.string(),
    cycle_id: t.string(),
    knowledge_sim_ms: t.f64(),
    danger_json: t.string(),
    burned_ha: t.f64(),
  }
);

const zone = table(
  { name: 'zone', public: true },
  {
    zone_id: t.string().primaryKey(),
    scenario_id: t.string().index('btree'),
    name: t.string(),
    population: t.u32(),
    housing: t.u32(),
    n_critical: t.u32(),
    tier: t.u32(),
    evac_min_default: t.f64(),
    egress_roads: t.u32(),
    approximate: t.bool(),
    evac_min: t.f64(),
  }
);

const zone_risk = table(
  { name: 'zone_risk', public: true },
  {
    id: t.string().primaryKey(), // {cycle_id}:{zone_id}
    cycle_id: t.string(),
    scenario_id: t.string().index('btree'),
    branch_id: t.string(),
    zone_id: t.string(),
    knowledge_sim_ms: t.f64(),
    p_by_lead_json: t.string(),
    eta_q10_sim_ms: t.option(t.f64()),
    eta_q50_sim_ms: t.option(t.f64()),
    eta_q90_sim_ms: t.option(t.f64()),
    act_by_sim_ms: t.option(t.f64()),
    act_by_basis: t.string(),
    evac_min: t.f64(),
    population: t.u32(),
    housing: t.u32(),
    n_critical: t.u32(),
    tier: t.u32(),
    risk_score: t.f64(),
    rank: t.u32(),
    status: t.string(),
    trend: t.string(),
    drivers_json: t.string(),
  }
);

const alert = table(
  { name: 'alert', public: true },
  {
    alert_id: t.string().primaryKey(),
    scenario_id: t.string().index('btree'),
    branch_id: t.string(),
    zone_ids_json: t.string(),
    level: t.string(),
    rule_id: t.string(),
    rule_text: t.string(),
    cycle_id: t.string(),
    triggered_sim_ms: t.f64(),
    act_by_sim_ms: t.option(t.f64()),
    state: t.string(),
    text: t.string(),
    voice_script: t.string(),
    draft_source: t.string(),
    briefing_id: t.option(t.string()),
    decided_by: t.option(t.string()),
    decided_wall_ms: t.option(t.f64()),
    call_id: t.option(t.string()),
    call_status: t.option(t.string()),
    acked_via: t.option(t.string()),
    acked_wall_ms: t.option(t.f64()),
    acked_sim_ms: t.option(t.f64()),
  }
);

const briefing = table(
  { name: 'briefing', public: true },
  {
    briefing_id: t.string().primaryKey(),
    scenario_id: t.string().index('btree'),
    branch_id: t.string(),
    cycle_id: t.string(),
    alert_id: t.string(),
    model: t.string(),
    prompt_version: t.string(),
    input_hash: t.string(),
    headline: t.string(),
    bullets_json: t.string(),
    text: t.string(),
    voice_script: t.string(),
    grounded: t.bool(),
    unmatched_tokens_json: t.string(),
    fallback_used: t.bool(),
    latency_ms: t.u32(),
    created_wall_ms: t.f64(),
  }
);

const person = table(
  { name: 'person', public: true },
  {
    person_id: t.string().primaryKey(),
    scenario_id: t.string().index('btree'),
    kind: t.string(),
    label: t.string(),
    lon: t.f64(),
    lat: t.f64(),
    location_source: t.string(),
    zone_id: t.option(t.string()),
    status: t.string(),
    p_60: t.option(t.f64()),
    eta_q50_sim_ms: t.option(t.f64()),
    act_by_sim_ms: t.option(t.f64()),
    alerted_alert_id: t.option(t.string()),
    acknowledged: t.bool(),
    last_seen_wall_ms: t.f64(),
    provenance: t.string(),
  }
);

const spawn_point = table(
  { name: 'spawn_point', public: true },
  {
    spawn_id: t.string().primaryKey(),
    scenario_id: t.string().index('btree'),
    join_code: t.string(),
    ord: t.u32(), // position in spawn_points.json; "first free" follows it
    lon: t.f64(),
    lat: t.f64(),
    zone_id: t.string(),
    street: t.string(),
    taken_by: t.option(t.string()),
    taken_wall_ms: t.option(t.f64()), // for "reuse the least recently taken"
  }
);

const report = table(
  { name: 'report', public: true },
  {
    report_id: t.string().primaryKey(),
    scenario_id: t.string().index('btree'),
    branch_id: t.string(),
    person_id: t.option(t.string()),
    channel: t.string(),
    kind: t.string(),
    summary: t.string(),
    lon: t.f64(),
    lat: t.f64(),
    radius_m: t.f64(),
    trust: t.f64(),
    observed_sim_ms: t.f64(),
    created_wall_ms: t.f64(),
    status: t.string(),
    reject_reason: t.option(t.string()),
    observation_id: t.option(t.string()),
    provenance: t.string(),
  }
);

const report_text = table(
  { name: 'report_text' }, // private
  {
    report_id: t.string().primaryKey(),
    transcript: t.string(),
  }
);

const activity_event = table(
  { name: 'activity_event', public: true },
  {
    event_id: t.string().primaryKey(),
    scenario_id: t.string().index('btree'),
    branch_id: t.string(),
    seq: t.u32(),
    sim_ms: t.f64(),
    wall_ms: t.f64(),
    stage: t.string(),
    actor: t.string(),
    level: t.string(),
    message: t.string(),
    ref_kind: t.option(t.string()),
    ref_id: t.option(t.string()),
    duration_ms: t.option(t.i32()),
  }
);

const metric = table(
  { name: 'metric', public: true },
  {
    id: t.string().primaryKey(), // {branch}:{key}
    key: t.string(),
    scenario_id: t.string().index('btree'),
    branch_id: t.string(),
    label: t.string(),
    value: t.option(t.f64()),
    unit: t.string(),
    definition: t.string(),
    computed_sim_ms: t.option(t.f64()),
    detail_json: t.string(),
  }
);

const command = table(
  { name: 'command', public: true },
  {
    command_id: t.string().primaryKey(),
    scenario_id: t.string(),
    kind: t.string(),
    args_json: t.string(),
    status: t.string().index('btree'),
    created_wall_ms: t.f64(),
    actor: t.string(),
    result_json: t.option(t.string()),
  }
);

const spacetimedb = schema({
  config, scenario, clock, branch, sensor, scan_event, observation, cycle, cycle_head, zone, zone_risk,
  alert, briefing, person, spawn_point, report, report_text, activity_event, metric, command,
});
export default spacetimedb;

// ---------------------------------------------------------------- helpers

type Ctx = ReducerCtx<InferSchema<typeof spacetimedb>>;
type Clock = NonNullable<ReturnType<Ctx['db']['clock']['scenario_id']['find']>>;
type Alert = NonNullable<ReturnType<Ctx['db']['alert']['alert_id']['find']>>;
// Parsed JSON from trusted engine/service payloads (pydantic-validated on the engine side).
type Json = Record<string, any>;

const optNum = (v: unknown): number | undefined => (v === null || v === undefined ? undefined : Number(v));
const optStr = (v: unknown): string | undefined => (v === null || v === undefined || v === '' ? undefined : String(v));
const json = (v: unknown): string => JSON.stringify(v ?? null);
const u32 = (v: unknown): number => Math.max(0, Math.round(Number(v)));

function parse(text: string, what: string): any {
  try {
    return JSON.parse(text);
  } catch {
    throw new SenderError(`${what}: invalid JSON`);
  }
}

function nowMs(ctx: Ctx): number {
  return Number(ctx.timestamp.microsSinceUnixEpoch / 1000n);
}

function simNow(c: Clock, now: number): number {
  const raw = c.anchor_sim_ms + (c.playing ? (now - c.anchor_wall_ms) * c.speed : 0);
  return Math.min(Math.max(raw, c.min_sim_ms), c.max_sim_ms);
}

function reanchor(c: Clock, now: number): Clock {
  return { ...c, anchor_sim_ms: simNow(c, now), anchor_wall_ms: now };
}

function getConfig(ctx: Ctx) {
  const cfg = ctx.db.config.id.find('singleton');
  if (!cfg) throw new SenderError('hub not initialised: call set_identities');
  return cfg;
}

function requireEngine(ctx: Ctx): void {
  if (ctx.sender.toHexString() !== getConfig(ctx).engine_identity) throw new SenderError('engine identity required');
}

function requireService(ctx: Ctx): void {
  const cfg = getConfig(ctx);
  const me = ctx.sender.toHexString();
  if (me !== cfg.service_identity && me !== cfg.engine_identity) throw new SenderError('service identity required');
}

function requirePin(ctx: Ctx, pin: string): void {
  if (pin !== getConfig(ctx).operator_pin) throw new SenderError('bad operator pin');
}

function requireClock(ctx: Ctx, scenario_id: string): Clock {
  const c = ctx.db.clock.scenario_id.find(scenario_id);
  if (!c) throw new SenderError(`no clock for ${scenario_id}: load_scenario first`);
  return c;
}

function requireAlert(ctx: Ctx, alert_id: string): Alert {
  const a = ctx.db.alert.alert_id.find(alert_id);
  if (!a) throw new SenderError(`unknown alert ${alert_id}`);
  return a;
}

function insertCommand(ctx: Ctx, scenario_id: string, kind: string, args: object, actor = 'operator'): string {
  const us = ctx.timestamp.microsSinceUnixEpoch;
  let i = 0;
  while (ctx.db.command.command_id.find(`cmd_${us}_${i}`)) i++;
  const command_id = `cmd_${us}_${i}`;
  ctx.db.command.insert({
    command_id, scenario_id, kind, args_json: JSON.stringify(args), status: 'pending',
    created_wall_ms: nowMs(ctx), actor, result_json: undefined,
  });
  return command_id;
}

/** Insert events, skipping known event_ids; seq = max+1 per scenario; prune to MAX_EVENTS. */
function insertEvents(ctx: Ctx, events: Json[]): void {
  const touched = new Set<string>();
  const maxSeq = new Map<string, number>();
  for (const e of events) {
    if (ctx.db.activity_event.event_id.find(e.event_id)) continue;
    const sid = String(e.scenario_id);
    if (!maxSeq.has(sid)) {
      let m = 0;
      for (const r of ctx.db.activity_event.scenario_id.filter(sid)) m = Math.max(m, r.seq);
      maxSeq.set(sid, m);
    }
    const seq = maxSeq.get(sid)! + 1;
    maxSeq.set(sid, seq);
    touched.add(sid);
    ctx.db.activity_event.insert({
      event_id: String(e.event_id), scenario_id: sid, branch_id: String(e.branch_id ?? 'main'), seq,
      sim_ms: Number(e.sim_ms), wall_ms: Number(e.wall_ms), stage: String(e.stage), actor: String(e.actor),
      level: String(e.level ?? 'info'), message: String(e.message).slice(0, 140),
      ref_kind: optStr(e.ref_kind), ref_id: optStr(e.ref_id),
      duration_ms: e.duration_ms === null || e.duration_ms === undefined ? undefined : Math.round(Number(e.duration_ms)),
    });
  }
  for (const sid of touched) {
    const rows = [...ctx.db.activity_event.scenario_id.filter(sid)];
    if (rows.length <= MAX_EVENTS) continue;
    rows.sort((a, b) => a.seq - b.seq);
    for (const r of rows.slice(0, rows.length - MAX_EVENTS)) ctx.db.activity_event.event_id.delete(r.event_id);
  }
}

/** Hub-made event. The id carries the context timestamp so a re-proposed alert (after rewind/reset) logs again. */
function hubEvent(ctx: Ctx, scenario_id: string, branch_id: string, prefix: string, sim_ms: number,
                  stage: string, actor: string, message: string, ref_kind?: string, ref_id?: string): void {
  insertEvents(ctx, [{
    event_id: `${prefix}_${ctx.timestamp.microsSinceUnixEpoch}`, scenario_id, branch_id, sim_ms, wall_ms: nowMs(ctx), stage, actor,
    level: 'notice', message, ref_kind, ref_id, duration_ms: null,
  }]);
}

function upsertBy<R>(find: R | null | undefined, insert: () => void, update: () => void): void {
  if (find) update();
  else insert();
}

// ---- row builders: contract JSON -> hub rows

function cycleRow(c: Json) {
  return {
    cycle_id: String(c.cycle_id), scenario_id: String(c.scenario_id), branch_id: String(c.branch_id),
    knowledge_sim_ms: Number(c.knowledge_sim_ms), data_through_sim_ms: Number(c.data_through_sim_ms),
    created_wall_ms: Number(c.created_wall_ms), source: String(c.source), filter_mode: String(c.filter_mode),
    n_members: u32(c.n_members), ess: Number(c.ess), tempering_alpha: Number(c.tempering_alpha),
    resampled: Boolean(c.resampled), n_obs_by_kind_json: json(c.n_obs_by_kind),
    horizon_min: u32(c.horizon_min ?? 360), step_min: u32(c.step_min ?? 30),
    assets_json: json(c.assets), danger_json: json(c.danger), burned_ha: Number(c.burned_ha),
    front_km: Number(c.front_km), growth_ha_per_h: Number(c.growth_ha_per_h),
    head_bearing_deg: optNum(c.head_bearing_deg), params_summary_json: json(c.params_summary),
    model_version: String(c.model_version), config_hash: optStr(c.config_hash), duration_ms: u32(c.duration_ms),
  };
}

function zoneRiskRow(z: Json) {
  return {
    id: `${z.cycle_id}:${z.zone_id}`, cycle_id: String(z.cycle_id), scenario_id: String(z.scenario_id),
    branch_id: String(z.branch_id), zone_id: String(z.zone_id), knowledge_sim_ms: Number(z.knowledge_sim_ms),
    p_by_lead_json: json(z.p_by_lead), eta_q10_sim_ms: optNum(z.eta_q10_sim_ms),
    eta_q50_sim_ms: optNum(z.eta_q50_sim_ms), eta_q90_sim_ms: optNum(z.eta_q90_sim_ms),
    act_by_sim_ms: optNum(z.act_by_sim_ms), act_by_basis: String(z.act_by_basis), evac_min: Number(z.evac_min),
    population: u32(z.population), housing: u32(z.housing), n_critical: u32(z.n_critical), tier: u32(z.tier),
    risk_score: Number(z.risk_score), rank: u32(z.rank), status: String(z.status), trend: String(z.trend),
    drivers_json: json(z.drivers),
  };
}

function observationRow(o: Json) {
  return {
    obs_id: String(o.obs_id), scenario_id: String(o.scenario_id), branch_id: String(o.branch_id ?? 'main'),
    sensor_id: String(o.sensor_id), kind: String(o.kind), observed_sim_ms: Number(o.observed_sim_ms),
    available_sim_ms: Number(o.available_sim_ms), geometry_json: json(o.geometry), hot: Boolean(o.hot),
    footprint_key: optStr(o.footprint_key),
    fdc_class: o.fdc_class === null || o.fdc_class === undefined ? undefined : Math.round(Number(o.fdc_class)),
    report_kind: optStr(o.report_kind), confidence: Number(o.confidence ?? 1), frp_mw: optNum(o.frp_mw),
    trust: Number(o.trust ?? 1), provenance: String(o.provenance), source_ref: optStr(o.source_ref),
    status: String(o.status ?? 'pending'), cycle_id: optStr(o.cycle_id), influence: optNum(o.influence),
  };
}

function scanRow(s: Json) {
  return {
    scan_id: String(s.scan_id), scenario_id: String(s.scenario_id), branch_id: String(s.branch_id ?? 'main'),
    sensor_id: String(s.sensor_id), mode: String(s.mode), t_start_sim_ms: Number(s.t_start_sim_ms),
    t_end_sim_ms: Number(s.t_end_sim_ms), available_sim_ms: Number(s.available_sim_ms),
    n_pixels_aoi: u32(s.n_pixels_aoi), n_hot: u32(s.n_hot), n_clear: u32(s.n_clear),
    assimilated: s.assimilated ?? true, pixels_path: optStr(s.pixels_path), provenance: String(s.provenance ?? 'real'),
  };
}

function sensorRow(s: Json) {
  return {
    sensor_id: String(s.sensor_id), scenario_id: String(s.scenario_id), kind: String(s.kind), name: String(s.name),
    platform: String(s.platform), provenance: String(s.provenance), enabled: s.enabled ?? true,
    status: String(s.status ?? 'ok'), last_data_sim_ms: optNum(s.last_data_sim_ms), latency_s: optNum(s.latency_s),
    lon: optNum(s.lon), lat: optNum(s.lat), heading_deg: optNum(s.heading_deg), hfov_deg: optNum(s.hfov_deg),
    pixel_m: optNum(s.pixel_m),
  };
}

function metricRow(m: Json) {
  return {
    id: `${m.branch_id}:${m.key}`, key: String(m.key), scenario_id: String(m.scenario_id),
    branch_id: String(m.branch_id), label: String(m.label), value: optNum(m.value), unit: String(m.unit),
    definition: String(m.definition), computed_sim_ms: optNum(m.computed_sim_ms), detail_json: json(m.detail ?? {}),
  };
}

function alertRow(a: Json) {
  return {
    alert_id: String(a.alert_id), scenario_id: String(a.scenario_id), branch_id: String(a.branch_id),
    zone_ids_json: json(a.zone_ids), level: String(a.level), rule_id: String(a.rule_id),
    rule_text: String(a.rule_text), cycle_id: String(a.cycle_id), triggered_sim_ms: Number(a.triggered_sim_ms),
    act_by_sim_ms: optNum(a.act_by_sim_ms), state: String(a.state ?? 'proposed'), text: String(a.text),
    voice_script: String(a.voice_script), draft_source: String(a.draft_source ?? 'template'),
    briefing_id: optStr(a.briefing_id), decided_by: optStr(a.decided_by), decided_wall_ms: optNum(a.decided_wall_ms),
    call_id: optStr(a.call_id), call_status: optStr(a.call_status), acked_via: optStr(a.acked_via),
    acked_wall_ms: optNum(a.acked_wall_ms), acked_sim_ms: optNum(a.acked_sim_ms),
  };
}

function briefingRow(b: Json, scenario_id: string) {
  return {
    briefing_id: String(b.briefing_id), scenario_id: String(b.scenario_id ?? scenario_id),
    branch_id: String(b.branch_id), cycle_id: String(b.cycle_id), alert_id: String(b.alert_id),
    model: String(b.model), prompt_version: String(b.prompt_version), input_hash: String(b.input_hash ?? ''),
    headline: String(b.headline), bullets_json: json(b.bullets), text: String(b.text),
    voice_script: String(b.voice_script), grounded: Boolean(b.grounded),
    unmatched_tokens_json: json(b.unmatched_tokens ?? []), fallback_used: Boolean(b.fallback_used),
    latency_ms: u32(b.latency_ms), created_wall_ms: Number(b.created_wall_ms),
  };
}

function setCycleHead(ctx: Ctx, scenario_id: string, branch_id: string): void {
  // Recompute from the cycle table: the newest knowledge_sim_ms of this branch.
  const id = `${scenario_id}:${branch_id}`;
  let best: ReturnType<typeof cycleRow> | null = null;
  for (const c of ctx.db.cycle.scenario_id.filter(scenario_id)) {
    if (c.branch_id === branch_id && (!best || c.knowledge_sim_ms > best.knowledge_sim_ms)) best = c;
  }
  ctx.db.cycle_head.id.delete(id);
  if (best) {
    ctx.db.cycle_head.insert({
      id, scenario_id, branch_id, cycle_id: best.cycle_id, knowledge_sim_ms: best.knowledge_sim_ms,
      danger_json: best.danger_json, burned_ha: best.burned_ha,
    });
  }
}

function insertReport(ctx: Ctx, r: Json, transcript: string): void {
  const report_id = String(r.report_id);
  if (!report_id) throw new SenderError('report_id required');
  if (ctx.db.report.report_id.find(report_id)) throw new SenderError(`report ${report_id} exists`);
  const scenario_id = String(r.scenario_id);
  ctx.db.report.insert({
    report_id, scenario_id, branch_id: String(r.branch_id ?? 'main'), person_id: optStr(r.person_id),
    channel: String(r.channel), kind: String(r.kind), summary: String(r.summary), lon: Number(r.lon),
    lat: Number(r.lat), radius_m: Number(r.radius_m), trust: Number(r.trust ?? 0.5),
    observed_sim_ms: Number(r.observed_sim_ms), created_wall_ms: nowMs(ctx), status: 'pending',
    reject_reason: undefined, observation_id: undefined, provenance: String(r.provenance ?? 'injected'),
  });
  ctx.db.report_text.insert({ report_id, transcript });
  insertCommand(ctx, scenario_id, 'report', { report_id }, 'service');
}

/** publish_bundle steps (1)-(7). Returns the ids of alerts newly inserted as `proposed`. */
function applyBundle(ctx: Ctx, b: Json): string[] {
  const db = ctx.db;
  const scenario_id = String(b.scenario_id);

  // (1) cycle + cycle_head (advance only forward; rewind recomputes)
  if (b.cycle) {
    const row = cycleRow(b.cycle);
    upsertBy(db.cycle.cycle_id.find(row.cycle_id), () => db.cycle.insert(row), () => db.cycle.cycle_id.update(row));
    const id = `${row.scenario_id}:${row.branch_id}`;
    const head = db.cycle_head.id.find(id);
    const next = {
      id, scenario_id: row.scenario_id, branch_id: row.branch_id, cycle_id: row.cycle_id,
      knowledge_sim_ms: row.knowledge_sim_ms, danger_json: row.danger_json, burned_ha: row.burned_ha,
    };
    if (!head) db.cycle_head.insert(next);
    else if (row.knowledge_sim_ms >= head.knowledge_sim_ms) db.cycle_head.id.update(next);
  }

  // (2) zone_risk, observation (insert-or-ignore), scan_event, sensor deltas, metric
  for (const z of b.zone_risk ?? []) {
    const row = zoneRiskRow(z);
    upsertBy(db.zone_risk.id.find(row.id), () => db.zone_risk.insert(row), () => db.zone_risk.id.update(row));
  }
  for (const o of b.observations ?? []) {
    if (!db.observation.obs_id.find(String(o.obs_id))) db.observation.insert(observationRow(o));
  }
  // insert-or-ignore like observations: scan_id has no branch, so a branch republishing a real scan
  // must not take over main's row (rewinding the branch would then delete it)
  for (const s of b.scan_events ?? []) {
    if (!db.scan_event.scan_id.find(String(s.scan_id))) db.scan_event.insert(scanRow(s));
  }
  for (const s of b.sensors ?? []) {
    const row = sensorRow(s);
    upsertBy(db.sensor.sensor_id.find(row.sensor_id), () => db.sensor.insert(row), () => db.sensor.sensor_id.update(row));
  }
  for (const m of b.metrics ?? []) {
    const row = metricRow(m);
    upsertBy(db.metric.id.find(row.id), () => db.metric.insert(row), () => db.metric.id.update(row));
  }

  // (3) alerts: insert new; on existing rows only proposed -> superseded
  const newProposals: string[] = [];
  for (const a of b.alerts ?? []) {
    const row = alertRow(a);
    const existing = db.alert.alert_id.find(row.alert_id);
    if (!existing) {
      db.alert.insert(row);
      if (row.state === 'proposed') newProposals.push(row.alert_id);
    } else if (existing.state === 'proposed' && row.state === 'superseded') {
      db.alert.alert_id.update({ ...existing, state: 'superseded' });
    }
  }

  // (4) briefings: insert-or-ignore
  for (const br of b.briefings ?? []) {
    if (!db.briefing.briefing_id.find(String(br.briefing_id))) db.briefing.insert(briefingRow(br, scenario_id));
  }

  // (5) person risk fields; unknown people skipped
  for (const p of b.people ?? []) {
    const existing = db.person.person_id.find(String(p.person_id));
    if (!existing) continue;
    db.person.person_id.update({
      ...existing, p_60: optNum(p.p_60), eta_q50_sim_ms: optNum(p.eta_q50_sim_ms), act_by_sim_ms: optNum(p.act_by_sim_ms),
    });
  }

  // (6) report statuses: pending -> accepted/rejected
  for (const r of b.reports ?? []) {
    const existing = db.report.report_id.find(String(r.report_id));
    if (!existing || existing.status !== 'pending') continue;
    db.report.report_id.update({
      ...existing, status: String(r.status), reject_reason: optStr(r.reject_reason), observation_id: optStr(r.observation_id),
    });
  }

  // (7) events
  insertEvents(ctx, b.events ?? []);
  return newProposals;
}

/** publish_bundle step (8): pause for operator review on a newly inserted proposal. */
function pauseOnProposal(ctx: Ctx, b: Json, newProposals: string[]): void {
  if (newProposals.length === 0) return;
  const c = ctx.db.clock.scenario_id.find(String(b.scenario_id));
  if (!c || !c.pause_on_proposal) return;
  const now = nowMs(ctx);
  // resume_playing too: a proposal published while `computing` must stay paused after set_clock_status(ok)
  const next = { ...reanchor(c, now), playing: false, resume_playing: false };
  ctx.db.clock.scenario_id.update(next);
  hubEvent(ctx, c.scenario_id, String(b.branch_id), `ev_pause_${newProposals[0]}`, next.anchor_sim_ms,
           'system', 'hub', 'Clock paused for operator review', 'alert', newProposals[0]);
}

/** Delete one branch's rows newer than sim_ms (rewind and reset share this list). */
function deleteAfter(ctx: Ctx, scenario_id: string, branch_id: string, sim_ms: number): void {
  const db = ctx.db;
  const mine = <R extends { branch_id: string }>(rows: Iterable<R>) => [...rows].filter(r => r.branch_id === branch_id);
  for (const r of mine(db.cycle.scenario_id.filter(scenario_id))) if (r.knowledge_sim_ms > sim_ms) db.cycle.cycle_id.delete(r.cycle_id);
  for (const r of mine(db.zone_risk.scenario_id.filter(scenario_id))) if (r.knowledge_sim_ms > sim_ms) db.zone_risk.id.delete(r.id);
  for (const r of mine(db.observation.scenario_id.filter(scenario_id))) if (r.available_sim_ms > sim_ms) db.observation.obs_id.delete(r.obs_id);
  for (const r of mine(db.scan_event.scenario_id.filter(scenario_id))) if (r.t_start_sim_ms > sim_ms) db.scan_event.scan_id.delete(r.scan_id);
  const goneAlerts = new Set<string>();
  for (const r of mine(db.alert.scenario_id.filter(scenario_id))) {
    if (r.triggered_sim_ms > sim_ms) {
      goneAlerts.add(r.alert_id);
      db.alert.alert_id.delete(r.alert_id);
    }
  }
  for (const r of [...db.briefing.scenario_id.filter(scenario_id)]) if (goneAlerts.has(r.alert_id)) db.briefing.briefing_id.delete(r.briefing_id);
  for (const r of mine(db.metric.scenario_id.filter(scenario_id))) {
    if (r.computed_sim_ms !== undefined && r.computed_sim_ms > sim_ms) db.metric.id.delete(r.id);
  }
  setCycleHead(ctx, scenario_id, branch_id);
}

function bumpEpoch(ctx: Ctx, scenario_id: string): void {
  const c = ctx.db.clock.scenario_id.find(scenario_id);
  if (c) ctx.db.clock.scenario_id.update({ ...c, epoch: c.epoch + 1 });
}

// ---------------------------------------------------------------- bootstrap

export const set_identities = spacetimedb.reducer(
  { engine_identity: t.string(), service_identity: t.string(), operator_pin: t.string() },
  (ctx, { engine_identity, service_identity, operator_pin }) => {
    if (ctx.db.config.id.find('singleton')) throw new SenderError('identities already set');
    if (!operator_pin) throw new SenderError('operator_pin must be non-empty');
    const hex = (s: string) => s.toLowerCase().replace(/^0x/, '');
    ctx.db.config.insert({
      id: 'singleton', engine_identity: hex(engine_identity), service_identity: hex(service_identity), operator_pin,
    });
  }
);

// ---------------------------------------------------------------- engine reducers

export const load_scenario = spacetimedb.reducer({ scenario_json: t.string() }, (ctx, { scenario_json }) => {
  requireEngine(ctx);
  const s = parse(scenario_json, 'scenario_json');
  const db = ctx.db;
  const scenario_id = String(s.scenario_id);
  const start = Number(s.replay_start_sim_ms);
  const end = Number(s.replay_end_sim_ms);
  const scn = {
    scenario_id, title: String(s.title), tz: String(s.tz), manifest_json: json(s.manifest),
    config_hash: optStr(s.config_hash), replay_start_sim_ms: start, replay_end_sim_ms: end,
  };
  upsertBy(db.scenario.scenario_id.find(scenario_id), () => db.scenario.insert(scn), () => db.scenario.scenario_id.update(scn));

  for (const z of s.zones ?? []) {
    const existing = db.zone.zone_id.find(String(z.zone_id));
    const row = {
      zone_id: String(z.zone_id), scenario_id, name: String(z.name), population: u32(z.population),
      housing: u32(z.housing), n_critical: u32(z.n_critical), tier: u32(z.tier),
      evac_min_default: Number(z.evac_min_default), egress_roads: u32(z.egress_roads),
      approximate: Boolean(z.approximate),
      // an operator's set_evac_min survives an engine restart
      evac_min: existing ? existing.evac_min : Number(z.evac_min ?? z.evac_min_default),
    };
    upsertBy(existing, () => db.zone.insert(row), () => db.zone.zone_id.update(row));
  }
  for (const se of s.sensors ?? []) {
    const row = sensorRow({ ...se, scenario_id });
    upsertBy(db.sensor.sensor_id.find(row.sensor_id), () => db.sensor.insert(row), () => db.sensor.sensor_id.update(row));
  }
  (s.spawn_points ?? []).forEach((sp: Json, i: number) => {
    const existing = db.spawn_point.spawn_id.find(String(sp.spawn_id));
    const row = {
      spawn_id: String(sp.spawn_id), scenario_id, join_code: String(sp.join_code), ord: i,
      lon: Number(sp.lon), lat: Number(sp.lat), zone_id: String(sp.zone_id), street: String(sp.street),
      taken_by: existing?.taken_by, taken_wall_ms: existing?.taken_wall_ms,
    };
    upsertBy(existing, () => db.spawn_point.insert(row), () => db.spawn_point.spawn_id.update(row));
  });
  // branch_id is the PK, so one `main` row is shared: point it at the scenario loaded last
  // (fixture first, then eaton, must leave eaton's `branch WHERE scenario_id` with a main row)
  const main = db.branch.branch_id.find('main');
  if (!main) {
    db.branch.insert({
      branch_id: 'main', scenario_id, parent_id: undefined, fork_sim_ms: undefined, reason: 'main',
      created_wall_ms: nowMs(ctx),
    });
  } else if (main.scenario_id !== scenario_id) {
    db.branch.branch_id.update({ ...main, scenario_id });
  }
  if (!db.clock.scenario_id.find(scenario_id)) {
    db.clock.insert({
      scenario_id, mode: 'replay', playing: false, speed: 240, anchor_sim_ms: start, anchor_wall_ms: nowMs(ctx),
      active_branch_id: 'main', status: 'ok', resume_playing: false, pause_on_proposal: true, epoch: 0,
      min_sim_ms: start, max_sim_ms: end,
    });
  }
});

export const publish_bundle = spacetimedb.reducer({ bundle_json: t.string() }, (ctx, { bundle_json }) => {
  requireEngine(ctx);
  const b = parse(bundle_json, 'bundle_json');
  pauseOnProposal(ctx, b, applyBundle(ctx, b));
});

export const publish_bundles = spacetimedb.reducer({ bundles_json: t.string() }, (ctx, { bundles_json }) => {
  requireEngine(ctx);
  const bundles = parse(bundles_json, 'bundles_json');
  if (!Array.isArray(bundles) || bundles.length === 0) throw new SenderError('bundles_json must be a non-empty array');
  if (bundles.length > 10) throw new SenderError('at most 10 bundles per call');
  bundles.forEach((b: Json, i: number) => {
    const fresh = applyBundle(ctx, b);
    if (i === bundles.length - 1) pauseOnProposal(ctx, b, fresh);
  });
});

export const rewind = spacetimedb.reducer(
  { scenario_id: t.string(), branch_id: t.string(), sim_ms: t.f64() },
  (ctx, { scenario_id, branch_id, sim_ms }) => {
    requireEngine(ctx);
    deleteAfter(ctx, scenario_id, branch_id, sim_ms);
    bumpEpoch(ctx, scenario_id);
  }
);

export const set_clock_status = spacetimedb.reducer(
  { scenario_id: t.string(), status: t.string() },
  (ctx, { scenario_id, status }) => {
    requireEngine(ctx);
    if (!['ok', 'computing', 'catching_up'].includes(status)) throw new SenderError(`bad status ${status}`);
    const c = reanchor(requireClock(ctx, scenario_id), nowMs(ctx));
    let next = { ...c, status };
    if (status === 'computing' && c.status !== 'computing') next = { ...next, resume_playing: c.playing, playing: false };
    if (status === 'ok' && c.status === 'computing') next = { ...next, playing: c.resume_playing };
    ctx.db.clock.scenario_id.update(next);
  }
);

export const create_branch = spacetimedb.reducer({ branch_json: t.string() }, (ctx, { branch_json }) => {
  requireEngine(ctx);
  const b = parse(branch_json, 'branch_json');
  const branch_id = String(b.branch_id);
  const scenario_id = String(b.scenario_id);
  if (ctx.db.branch.branch_id.find(branch_id)) throw new SenderError(`branch ${branch_id} exists`);
  ctx.db.branch.insert({
    branch_id, scenario_id, parent_id: optStr(b.parent_id), fork_sim_ms: optNum(b.fork_sim_ms),
    reason: String(b.reason ?? ''), created_wall_ms: nowMs(ctx),
  });
  const c = requireClock(ctx, scenario_id);
  ctx.db.clock.scenario_id.update({ ...c, active_branch_id: branch_id, epoch: c.epoch + 1 });
});

export const upsert_alert_draft = spacetimedb.reducer(
  { alert_id: t.string(), text: t.string(), voice_script: t.string(), draft_source: t.string(), briefing_json: t.string() },
  (ctx, { alert_id, text, voice_script, draft_source, briefing_json }) => {
    requireEngine(ctx);
    const a = ctx.db.alert.alert_id.find(alert_id);
    const row = briefingRow(parse(briefing_json, 'briefing_json'), a?.scenario_id ?? '');
    upsertBy(ctx.db.briefing.briefing_id.find(row.briefing_id), () => ctx.db.briefing.insert(row),
             () => ctx.db.briefing.briefing_id.update(row));
    if (a && a.state === 'proposed') {
      ctx.db.alert.alert_id.update({ ...a, text, voice_script, draft_source, briefing_id: row.briefing_id });
    }
  }
);

const CALL_TRANSITIONS: Record<string, string[]> = { calling: ['approved'], called: ['calling'], failed: ['approved', 'calling'] };

export const update_alert_call = spacetimedb.reducer(
  { alert_id: t.string(), state: t.string(), call_id: t.string(), call_status: t.string() },
  (ctx, { alert_id, state, call_id, call_status }) => {
    requireEngine(ctx);
    const a = requireAlert(ctx, alert_id);
    const call = { call_id: optStr(call_id) ?? a.call_id, call_status: optStr(call_status) ?? a.call_status };
    if (a.state === 'acknowledged') {
      ctx.db.alert.alert_id.update({ ...a, ...call }); // never change state, never error
      return;
    }
    if (!(CALL_TRANSITIONS[state] ?? []).includes(a.state)) throw new SenderError(`illegal ${a.state} -> ${state}`);
    ctx.db.alert.alert_id.update({ ...a, ...call, state });
  }
);

export const append_events = spacetimedb.reducer({ events_json: t.string() }, (ctx, { events_json }) => {
  requireEngine(ctx);
  const events = parse(events_json, 'events_json');
  if (!Array.isArray(events)) throw new SenderError('events_json must be an array');
  insertEvents(ctx, events);
});

export const mark_command = spacetimedb.reducer(
  { command_id: t.string(), status: t.string(), result_json: t.string() },
  (ctx, { command_id, status, result_json }) => {
    requireEngine(ctx);
    if (!['pending', 'done', 'failed'].includes(status)) throw new SenderError(`bad status ${status}`);
    const c = ctx.db.command.command_id.find(command_id);
    if (!c) throw new SenderError(`unknown command ${command_id}`);
    ctx.db.command.command_id.update({ ...c, status, result_json: optStr(result_json) });
  }
);

export const submit_report_engine = spacetimedb.reducer(
  { report_json: t.string(), transcript: t.string() },
  (ctx, { report_json, transcript }) => {
    requireEngine(ctx);
    insertReport(ctx, parse(report_json, 'report_json'), transcript);
  }
);

// ---------------------------------------------------------------- service reducers

export const submit_report = spacetimedb.reducer(
  { report_json: t.string(), transcript: t.string() },
  (ctx, { report_json, transcript }) => {
    requireService(ctx);
    insertReport(ctx, parse(report_json, 'report_json'), transcript);
  }
);

export const ack_alert = spacetimedb.reducer({ alert_id: t.string(), via: t.string() }, (ctx, { alert_id, via }) => {
  requireService(ctx);
  if (via !== 'phone' && via !== 'dashboard') throw new SenderError(`bad via ${via}`);
  const a = requireAlert(ctx, alert_id);
  if (!['approved', 'calling', 'called'].includes(a.state)) throw new SenderError(`cannot ack a ${a.state} alert`);
  const now = nowMs(ctx);
  const c = ctx.db.clock.scenario_id.find(a.scenario_id);
  const sim = c ? simNow(c, now) : a.triggered_sim_ms;
  ctx.db.alert.alert_id.update({ ...a, state: 'acknowledged', acked_via: via, acked_wall_ms: now, acked_sim_ms: sim });
  hubEvent(ctx, a.scenario_id, a.branch_id, `ev_ack_${alert_id}`, sim, 'human', via, `Acknowledged by ${via}`, 'alert', alert_id);
});

// ---------------------------------------------------------------- operator reducers (pin last)

export const clock_play = spacetimedb.reducer({ scenario_id: t.string(), pin: t.string() }, (ctx, { scenario_id, pin }) => {
  requirePin(ctx, pin);
  const c = requireClock(ctx, scenario_id);
  if (c.status === 'computing') throw new SenderError('clock is computing');
  ctx.db.clock.scenario_id.update({ ...reanchor(c, nowMs(ctx)), playing: true });
  insertCommand(ctx, scenario_id, 'clock_play', {});
});

export const clock_pause = spacetimedb.reducer({ scenario_id: t.string(), pin: t.string() }, (ctx, { scenario_id, pin }) => {
  requirePin(ctx, pin);
  const c = requireClock(ctx, scenario_id);
  ctx.db.clock.scenario_id.update({ ...reanchor(c, nowMs(ctx)), playing: false });
  insertCommand(ctx, scenario_id, 'clock_pause', {});
});

export const clock_seek = spacetimedb.reducer(
  { scenario_id: t.string(), sim_ms: t.f64(), pin: t.string() },
  (ctx, { scenario_id, sim_ms, pin }) => {
    requirePin(ctx, pin);
    const c = requireClock(ctx, scenario_id);
    const now = nowMs(ctx);
    const from_sim_ms = simNow(c, now);
    const to = Math.min(Math.max(sim_ms, c.min_sim_ms), c.max_sim_ms);
    ctx.db.clock.scenario_id.update({ ...c, anchor_sim_ms: to, anchor_wall_ms: now, epoch: c.epoch + 1 });
    insertCommand(ctx, scenario_id, 'clock_seek', { sim_ms: to, from_sim_ms });
  }
);

export const clock_set_speed = spacetimedb.reducer(
  { scenario_id: t.string(), speed: t.f64(), pin: t.string() },
  (ctx, { scenario_id, speed, pin }) => {
    requirePin(ctx, pin);
    const c = requireClock(ctx, scenario_id);
    const s = Math.min(Math.max(speed, 30), 900);
    ctx.db.clock.scenario_id.update({ ...reanchor(c, nowMs(ctx)), speed: s });
    insertCommand(ctx, scenario_id, 'clock_speed', { speed: s });
  }
);

export const set_pause_on_proposal = spacetimedb.reducer(
  { scenario_id: t.string(), on: t.bool(), pin: t.string() },
  (ctx, { scenario_id, on, pin }) => {
    requirePin(ctx, pin);
    const c = requireClock(ctx, scenario_id);
    ctx.db.clock.scenario_id.update({ ...c, pause_on_proposal: on });
  }
);

export const reset_scenario = spacetimedb.reducer({ scenario_id: t.string(), pin: t.string() }, (ctx, { scenario_id, pin }) => {
  requirePin(ctx, pin);
  const db = ctx.db;
  const c = requireClock(ctx, scenario_id);
  const start = db.scenario.scenario_id.find(scenario_id)?.replay_start_sim_ms ?? c.min_sim_ms;
  const nonMain = <R extends { branch_id: string }>(rows: Iterable<R>) => [...rows].filter(r => r.branch_id !== 'main');

  for (const r of [...db.alert.scenario_id.filter(scenario_id)]) db.alert.alert_id.delete(r.alert_id);
  for (const r of [...db.briefing.scenario_id.filter(scenario_id)]) db.briefing.briefing_id.delete(r.briefing_id);
  for (const r of [...db.report.scenario_id.filter(scenario_id)]) {
    db.report.report_id.delete(r.report_id);
    db.report_text.report_id.delete(r.report_id);
  }
  for (const r of [...db.person.scenario_id.filter(scenario_id)]) db.person.person_id.delete(r.person_id);
  for (const r of [...db.command.iter()]) if (r.scenario_id === scenario_id) db.command.command_id.delete(r.command_id);
  for (const r of [...db.branch.scenario_id.filter(scenario_id)]) if (r.branch_id !== 'main') db.branch.branch_id.delete(r.branch_id);
  for (const r of nonMain(db.cycle.scenario_id.filter(scenario_id))) db.cycle.cycle_id.delete(r.cycle_id);
  for (const r of nonMain(db.cycle_head.scenario_id.filter(scenario_id))) db.cycle_head.id.delete(r.id);
  for (const r of nonMain(db.zone_risk.scenario_id.filter(scenario_id))) db.zone_risk.id.delete(r.id);
  for (const r of nonMain(db.observation.scenario_id.filter(scenario_id))) db.observation.obs_id.delete(r.obs_id);
  for (const r of nonMain(db.scan_event.scenario_id.filter(scenario_id))) db.scan_event.scan_id.delete(r.scan_id);
  for (const r of nonMain(db.metric.scenario_id.filter(scenario_id))) db.metric.id.delete(r.id);
  for (const r of nonMain(db.activity_event.scenario_id.filter(scenario_id))) db.activity_event.event_id.delete(r.event_id);
  deleteAfter(ctx, scenario_id, 'main', start);
  // Hard reset clears the whole feed; otherwise the last run's "Clock paused" / "Operator approved"
  // lines stay on screen. The engine republishes the replay_start bundle (and its events) right after.
  // Rewind keeps the feed: 04 lists no feed column for it.
  for (const r of [...db.activity_event.scenario_id.filter(scenario_id)]) db.activity_event.event_id.delete(r.event_id);
  for (const r of [...db.spawn_point.scenario_id.filter(scenario_id)]) {
    db.spawn_point.spawn_id.update({ ...r, taken_by: undefined, taken_wall_ms: undefined });
  }
  db.clock.scenario_id.update({
    ...c, playing: false, anchor_sim_ms: start, anchor_wall_ms: nowMs(ctx), active_branch_id: 'main',
    status: 'ok', resume_playing: false, epoch: c.epoch + 1,
  });
  insertCommand(ctx, scenario_id, 'reset', {});
});

export const approve_alert = spacetimedb.reducer(
  { alert_id: t.string(), operator: t.string(), edited_text: t.string(), pin: t.string() },
  (ctx, { alert_id, operator, edited_text, pin }) => {
    requirePin(ctx, pin);
    const a = requireAlert(ctx, alert_id);
    if (a.state !== 'proposed') throw new SenderError(`cannot approve a ${a.state} alert`);
    const who = operator.trim() || 'Operator';
    ctx.db.alert.alert_id.update({
      ...a, state: 'approved', decided_by: who, decided_wall_ms: nowMs(ctx),
      text: edited_text.trim() ? edited_text : a.text,
    });
    insertCommand(ctx, a.scenario_id, 'alert_approved', { alert_id, operator: who });
  }
);

export const decline_alert = spacetimedb.reducer(
  { alert_id: t.string(), operator: t.string(), reason: t.string(), pin: t.string() },
  (ctx, { alert_id, operator, reason, pin }) => {
    requirePin(ctx, pin);
    const a = requireAlert(ctx, alert_id);
    if (a.state !== 'proposed') throw new SenderError(`cannot decline a ${a.state} alert`);
    const who = operator.trim() || 'Operator';
    ctx.db.alert.alert_id.update({ ...a, state: 'declined', decided_by: who, decided_wall_ms: nowMs(ctx) });
    insertCommand(ctx, a.scenario_id, 'alert_declined', { alert_id, operator: who, reason });
  }
);

export const set_evac_min = spacetimedb.reducer(
  { zone_id: t.string(), minutes: t.f64(), pin: t.string() },
  (ctx, { zone_id, minutes, pin }) => {
    requirePin(ctx, pin);
    const z = ctx.db.zone.zone_id.find(zone_id);
    if (!z) throw new SenderError(`unknown zone ${zone_id}`);
    const m = Math.min(Math.max(minutes, 10), 240);
    ctx.db.zone.zone_id.update({ ...z, evac_min: m });
    insertCommand(ctx, z.scenario_id, 'evac_min', { zone_id, minutes: m });
  }
);

export const drop_sensor = spacetimedb.reducer(
  { sensor_id: t.string(), enabled: t.bool(), pin: t.string() },
  (ctx, { sensor_id, enabled, pin }) => {
    requirePin(ctx, pin);
    const s = ctx.db.sensor.sensor_id.find(sensor_id);
    if (!s) throw new SenderError(`unknown sensor ${sensor_id}`);
    ctx.db.sensor.sensor_id.update({ ...s, enabled, status: enabled ? 'ok' : 'down' });
    insertCommand(ctx, s.scenario_id, 'drop_sensor', { sensor_id, enabled });
  }
);

// ---------------------------------------------------------------- public reducers (phones)

const PERSON_STATUS = ['unknown', 'safe', 'evacuating', 'needs_help'];

export const join_as_person = spacetimedb.reducer(
  { scenario_id: t.string(), person_id: t.string(), join_code: t.string(), label: t.string() },
  (ctx, { scenario_id, person_id, join_code, label }) => {
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(person_id)) throw new SenderError('bad person_id');
    if (ctx.db.person.person_id.find(person_id)) return; // idempotent
    const spawns = [...ctx.db.spawn_point.scenario_id.filter(scenario_id)]
      .filter(s => s.join_code === join_code)
      .sort((a, b) => a.ord - b.ord);
    if (spawns.length === 0) throw new SenderError('unknown join code');
    const sp = spawns.find(s => s.taken_by === undefined)
      ?? [...spawns].sort((a, b) => (a.taken_wall_ms ?? 0) - (b.taken_wall_ms ?? 0))[0];
    const now = nowMs(ctx);
    ctx.db.spawn_point.spawn_id.update({ ...sp, taken_by: person_id, taken_wall_ms: now });
    ctx.db.person.insert({
      person_id, scenario_id, kind: 'judge', label: label.trim().slice(0, 40) || 'Guest', lon: sp.lon, lat: sp.lat,
      location_source: 'assigned', zone_id: sp.zone_id, status: 'unknown', p_60: undefined,
      eta_q50_sim_ms: undefined, act_by_sim_ms: undefined, alerted_alert_id: undefined, acknowledged: false,
      last_seen_wall_ms: now, provenance: 'simulated',
    });
    insertCommand(ctx, scenario_id, 'person_joined', { person_id }, 'public');
  }
);

export const set_person_status = spacetimedb.reducer(
  { person_id: t.string(), status: t.string() },
  (ctx, { person_id, status }) => {
    if (!PERSON_STATUS.includes(status)) throw new SenderError(`bad status ${status}`);
    const p = ctx.db.person.person_id.find(person_id);
    if (!p) throw new SenderError(`unknown person ${person_id}`);
    ctx.db.person.person_id.update({ ...p, status, last_seen_wall_ms: nowMs(ctx) });
  }
);
