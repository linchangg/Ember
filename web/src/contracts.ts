// Hand-written from spec/00_contracts.md §3.3. Python in engine/emberline/contracts.py is the source of truth.

declare namespace GeoJSON {
  interface Polygon {
    type: "Polygon";
    coordinates: number[][][];
  }
  interface MultiPolygon {
    type: "MultiPolygon";
    coordinates: number[][][][];
  }
  interface Feature<G = Polygon | MultiPolygon, P = Record<string, unknown>> {
    type: "Feature";
    geometry: G;
    properties: P;
  }
}

export type Ms = number;
export type Prov = "real" | "simulated" | "injected" | "derived";
export const QUANTILES = [0.1, 0.3, 0.5, 0.7, 0.9] as const;
export const LEADS_MIN = [30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330, 360] as const;
export const pAt = (p_by_lead: number[], leadMin: number) => p_by_lead[leadMin / 30 - 1];

export interface ClockState {
  scenario_id: string;
  mode: "replay" | "live";
  playing: boolean;
  speed: number;
  anchor_sim_ms: Ms;
  anchor_wall_ms: Ms;
  active_branch_id: string;
  status: "ok" | "computing" | "catching_up";
  resume_playing: boolean;
  pause_on_proposal: boolean;
  epoch: number;
  min_sim_ms: Ms;
  max_sim_ms: Ms;
}
export const simNow = (c: ClockState, wall = Date.now()) =>
  Math.min(
    c.max_sim_ms,
    Math.max(
      c.min_sim_ms,
      c.anchor_sim_ms + (c.playing ? (wall - c.anchor_wall_ms) * c.speed : 0),
    ),
  );

export interface Branch {
  branch_id: string;
  scenario_id: string;
  parent_id: string | null;
  fork_sim_ms: Ms | null;
  reason: string;
  created_wall_ms: Ms;
}
export interface Cycle {
  cycle_id: string;
  scenario_id: string;
  branch_id: string;
  knowledge_sim_ms: Ms;
  data_through_sim_ms: Ms;
  source: "cache" | "live";
  filter_mode: "pf" | "reinit" | "none";
  n_members: number;
  ess: number;
  tempering_alpha: number;
  resampled: boolean;
  n_obs_by_kind: Record<string, number>;
  horizon_min: number;
  step_min: number;
  assets: { arrival_q: string; perimeter: string; isochrones: string };
  danger: { lead60_p30?: GeoJSON.MultiPolygon; lead120_p30?: GeoJSON.MultiPolygon };
  burned_ha: number;
  front_km: number;
  growth_ha_per_h: number;
  head_bearing_deg: number | null;
  params_summary: Record<string, [number, number]>;
  model_version: string;
  config_hash: string | null;
  duration_ms: number;
}
export interface CycleHead {
  scenario_id: string;
  branch_id: string;
  cycle_id: string;
  knowledge_sim_ms: Ms;
  danger: Cycle["danger"];
  burned_ha: number;
}
export interface ZoneFeatureProps {
  zone_id: string;
  name: string;
  population: number;
  housing: number;
  n_critical: number;
  tier: 1 | 2 | 3;
  evac_min_default: number;
  egress_roads: number;
  approximate: boolean;
}
export type ZoneFeature = GeoJSON.Feature<GeoJSON.Polygon | GeoJSON.MultiPolygon, ZoneFeatureProps>;
export interface Zone extends ZoneFeatureProps {
  evac_min: number;
}
export interface ZoneRisk {
  cycle_id: string;
  scenario_id: string;
  branch_id: string;
  zone_id: string;
  knowledge_sim_ms: Ms;
  p_by_lead: number[];
  eta_q10_sim_ms: Ms | null;
  eta_q50_sim_ms: Ms | null;
  eta_q90_sim_ms: Ms | null;
  act_by_sim_ms: Ms | null;
  act_by_basis: string;
  evac_min: number;
  population: number;
  housing: number;
  n_critical: number;
  tier: 1 | 2 | 3;
  risk_score: number;
  rank: number;
  status: "clear" | "watch" | "warning" | "order";
  trend: "up" | "flat" | "down";
  drivers: string[];
}
export type AlertState =
  | "proposed"
  | "approved"
  | "declined"
  | "superseded"
  | "calling"
  | "called"
  | "acknowledged"
  | "failed";
export interface Alert {
  alert_id: string;
  scenario_id: string;
  branch_id: string;
  zone_ids: string[];
  level: "advisory" | "warning" | "order_recommended";
  rule_id: string;
  rule_text: string;
  cycle_id: string;
  triggered_sim_ms: Ms;
  act_by_sim_ms: Ms | null;
  state: AlertState;
  text: string;
  voice_script: string;
  draft_source: "template" | "claude";
  briefing_id: string | null;
  decided_by: string | null;
  decided_wall_ms: Ms | null;
  call_id: string | null;
  call_status: string | null;
  acked_via: "phone" | "dashboard" | null;
  acked_wall_ms: Ms | null;
  acked_sim_ms: Ms | null;
}
export interface Briefing {
  briefing_id: string;
  branch_id: string;
  cycle_id: string;
  alert_id: string;
  model: string;
  prompt_version: string;
  headline: string;
  bullets: string[];
  text: string;
  voice_script: string;
  grounded: boolean;
  unmatched_tokens: string[];
  fallback_used: boolean;
  latency_ms: number;
}
export type Geometry =
  | { type: "footprint"; ring: [number, number][] }
  | { type: "point_radius"; lon: number; lat: number; radius_m: number }
  | {
      type: "bearing_ray";
      lon: number;
      lat: number;
      bearing_deg: number;
      sigma_deg: number;
      max_range_m: number;
    };
export interface Observation {
  obs_id: string;
  scenario_id: string;
  branch_id: string;
  sensor_id: string;
  kind: "goes_pixel" | "viirs_pixel" | "camera_bearing" | "ground_report" | "thermal_footprint";
  observed_sim_ms: Ms;
  available_sim_ms: Ms;
  geometry: Geometry;
  hot: boolean;
  fdc_class: number | null;
  report_kind: "fire" | "smoke" | "embers" | "all_clear" | null;
  confidence: number;
  frp_mw: number | null;
  trust: number;
  provenance: Prov;
  status: "pending" | "assimilated" | "rejected";
  cycle_id: string | null;
  influence: number | null;
}
export interface ScanEvent {
  scan_id: string;
  scenario_id: string;
  branch_id: string;
  sensor_id: string;
  mode: "CONUS" | "MESO" | "FD" | "pass";
  t_start_sim_ms: Ms;
  t_end_sim_ms: Ms;
  available_sim_ms: Ms;
  n_pixels_aoi: number;
  n_hot: number;
  n_clear: number;
  assimilated: boolean;
  pixels_path: string | null;
  provenance: Prov;
}
export interface SensorInfo {
  sensor_id: string;
  kind: Observation["kind"];
  name: string;
  platform: string;
  provenance: Prov;
  enabled: boolean;
  status: "ok" | "stale" | "down";
  last_data_sim_ms: Ms | null;
  latency_s: number | null;
  lon: number | null;
  lat: number | null;
  heading_deg: number | null;
  hfov_deg: number | null;
  pixel_m: number | null;
}
export interface Person {
  person_id: string;
  kind: "resident" | "official" | "judge";
  label: string;
  lon: number;
  lat: number;
  location_source: "assigned" | "gps";
  zone_id: string | null;
  status: "unknown" | "safe" | "evacuating" | "needs_help";
  p_60: number | null;
  eta_q50_sim_ms: Ms | null;
  act_by_sim_ms: Ms | null;
  alerted_alert_id: string | null;
  acknowledged: boolean;
  last_seen_wall_ms: Ms;
  provenance: Prov;
}
export interface Report {
  report_id: string;
  branch_id: string;
  person_id: string | null;
  channel: "voice" | "tap" | "dashboard";
  kind: "fire" | "smoke" | "embers" | "all_clear";
  summary: string;
  lon: number;
  lat: number;
  radius_m: number;
  trust: number;
  observed_sim_ms: Ms;
  created_wall_ms: Ms;
  status: "pending" | "accepted" | "rejected";
  reject_reason: string | null;
  observation_id: string | null;
  provenance: Prov;
}
export interface ActivityEvent {
  event_id: string;
  seq: number;
  branch_id: string;
  sim_ms: Ms;
  wall_ms: Ms;
  stage:
    | "ingest"
    | "observation"
    | "filter"
    | "forecast"
    | "risk"
    | "alert"
    | "briefing"
    | "call"
    | "human"
    | "report"
    | "system";
  actor: string;
  level: "info" | "notice" | "warn" | "error";
  message: string;
  ref_kind: string | null;
  ref_id: string | null;
  duration_ms: number | null;
}
export interface Metric {
  key: string;
  branch_id: string;
  label: string;
  value: number | null;
  unit: string;
  definition: string;
  computed_sim_ms: Ms | null;
  detail: Record<string, unknown>;
}
export interface CycleBundle {
  schema_version: number;
  bundle_id: string;
  scenario_id: string;
  branch_id: string;
  knowledge_sim_ms: Ms;
  cycle: Cycle | null;
  zone_risk: ZoneRisk[];
  observations: Observation[];
  scan_events: ScanEvent[];
  sensors: SensorInfo[];
  alerts: Alert[];
  briefings: Briefing[];
  people: Person[];
  reports: {
    report_id: string;
    status: "accepted" | "rejected";
    reject_reason: string | null;
    observation_id: string | null;
  }[];
  metrics: Metric[];
  events: Omit<ActivityEvent, "seq">[];
}
