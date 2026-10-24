"""Shared contracts. spec/00_contracts.md §3 is the source of this file."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = 2
Ms = int
Prov = Literal["real", "simulated", "injected", "derived"]
QUANTILES = (0.1, 0.3, 0.5, 0.7, 0.9)
LEADS_MIN = tuple(range(30, 361, 30))  # 12 values; index i -> lead 30*(i+1)


def lead_idx(lead_min: int) -> int:
    """p_at(60) = p_by_lead[1]."""
    return lead_min // 30 - 1


GOES_FIRE = frozenset({10, 11, 12, 13, 14, 30, 31, 32, 33, 34})  # "hot"
GOES_STRONG = frozenset({10, 11, 12, 13, 30, 31, 32, 33})  # re-anchoring + honest number
REPORT_HOT = {
    "fire": (True, 1.0),
    "embers": (True, 1.0),
    "smoke": (True, 0.5),
    "all_clear": (False, 1.0),
}  # (hot, trust factor)


class Footprint(BaseModel):
    type: Literal["footprint"] = "footprint"
    ring: list[tuple[float, float]]  # closed lon/lat ring


class PointRadius(BaseModel):
    type: Literal["point_radius"] = "point_radius"
    lon: float
    lat: float
    radius_m: float


class BearingRay(BaseModel):
    type: Literal["bearing_ray"] = "bearing_ray"
    lon: float
    lat: float
    bearing_deg: float
    sigma_deg: float
    max_range_m: float


Geometry = Annotated[Footprint | PointRadius | BearingRay, Field(discriminator="type")]
ObsKind = Literal["goes_pixel", "viirs_pixel", "camera_bearing", "ground_report", "thermal_footprint"]


class ObservationRecord(BaseModel):
    obs_id: str
    scenario_id: str
    branch_id: str = "main"
    sensor_id: str
    kind: ObsKind
    observed_sim_ms: Ms
    available_sim_ms: Ms
    geometry: Geometry
    hot: bool  # False = negative evidence (clear pixel / all_clear report)
    footprint_key: str | None = None  # stable cache key, e.g. "goes18:{px_x}:{px_y}"
    fdc_class: int | None = None  # GOES Mask code when kind=goes_pixel
    report_kind: Literal["fire", "smoke", "embers", "all_clear"] | None = None
    confidence: float = 1.0  # display only (VIIRS l/n/h -> .3/.6/.9)
    frp_mw: float | None = None
    trust: float = 1.0  # r in L = (1-r) + r*L_sensor
    provenance: Prov
    source_ref: str | None = None
    status: Literal["pending", "assimilated", "rejected"] = "pending"
    cycle_id: str | None = None
    influence: float | None = None


class ScanEvent(BaseModel):
    scan_id: str
    scenario_id: str
    branch_id: str = "main"
    sensor_id: str
    mode: Literal["CONUS", "MESO", "FD", "pass"]
    t_start_sim_ms: Ms
    t_end_sim_ms: Ms
    available_sim_ms: Ms
    n_pixels_aoi: int
    n_hot: int
    n_clear: int
    assimilated: bool = True  # False = display-only (e.g. 1-min meso, P2)
    pixels_path: str | None = None  # static/{scn}/scans/{scan_id}.json (P2 flash)
    provenance: Prov = "real"


class SensorInfo(BaseModel):
    sensor_id: str
    scenario_id: str
    kind: ObsKind
    name: str
    platform: str
    provenance: Prov
    enabled: bool = True
    status: Literal["ok", "stale", "down"] = "ok"  # GOES: stale if no scan in 15 sim-min
    last_data_sim_ms: Ms | None = None
    latency_s: float | None = None
    lon: float | None = None
    lat: float | None = None
    heading_deg: float | None = None
    hfov_deg: float | None = None
    pixel_m: float | None = None


class CycleAssets(BaseModel):
    """ASSET_BASE-relative paths (spec/00 §4)."""

    arrival_q: str  # cache/{scn}/{branch}/{k}/arrival_q.bin
    perimeter: str  # cache/{scn}/{branch}/{k}/perimeter.geojson
    isochrones: str  # cache/{scn}/{branch}/{k}/isochrones.geojson


class Cycle(BaseModel):
    cycle_id: str
    scenario_id: str
    branch_id: str
    knowledge_sim_ms: Ms
    data_through_sim_ms: Ms
    created_wall_ms: Ms
    source: Literal["cache", "live"]  # set at publish time
    filter_mode: Literal["pf", "reinit", "none"]
    n_members: int
    ess: float
    tempering_alpha: float
    resampled: bool
    n_obs_by_kind: dict[str, int]
    horizon_min: int = 360
    step_min: int = 30
    assets: CycleAssets
    danger: dict  # {"lead60_p30": MultiPolygon, "lead120_p30": ...}; <= 20 KB
    burned_ha: float
    front_km: float
    growth_ha_per_h: float
    head_bearing_deg: float | None
    params_summary: dict  # {"ros_mult": [mean, sd], ...}
    model_version: str
    config_hash: str | None
    duration_ms: int


class ZoneRisk(BaseModel):
    cycle_id: str
    scenario_id: str
    branch_id: str
    zone_id: str
    knowledge_sim_ms: Ms
    p_by_lead: list[float]  # 12 values aligned to LEADS_MIN
    eta_q10_sim_ms: Ms | None
    eta_q50_sim_ms: Ms | None
    eta_q90_sim_ms: Ms | None
    act_by_sim_ms: Ms | None  # eta_q{act_by_quantile} - evac_min
    act_by_basis: str  # "q30 arrival − 45 min evac"
    evac_min: float
    population: int
    housing: int
    n_critical: int
    tier: Literal[1, 2, 3]
    risk_score: float
    rank: int
    status: Literal["clear", "watch", "warning", "order"]
    trend: Literal["up", "flat", "down"]
    drivers: list[str]


AlertState = Literal[
    "proposed",
    "approved",
    "declined",
    "superseded",
    "calling",
    "called",
    "acknowledged",
    "failed",
]


class Alert(BaseModel):
    alert_id: str
    scenario_id: str
    branch_id: str
    zone_ids: list[str]  # ranked; alert_id uses zone_ids[0]
    level: Literal["advisory", "warning", "order_recommended"]
    rule_id: str
    rule_text: str
    cycle_id: str
    triggered_sim_ms: Ms
    act_by_sim_ms: Ms | None
    state: AlertState = "proposed"
    text: str  # <= 300 chars
    voice_script: str  # <= 110 words
    draft_source: Literal["template", "claude"] = "template"
    briefing_id: str | None = None
    decided_by: str | None = None
    decided_wall_ms: Ms | None = None
    call_id: str | None = None
    call_status: str | None = None
    acked_via: Literal["phone", "dashboard"] | None = None
    acked_wall_ms: Ms | None = None
    acked_sim_ms: Ms | None = None  # UI shows "acknowledged 23:14 (replay time)"


class Briefing(BaseModel):
    briefing_id: str
    scenario_id: str
    branch_id: str
    cycle_id: str
    alert_id: str
    model: str
    prompt_version: str
    input_hash: str
    headline: str
    bullets: list[str]
    text: str
    voice_script: str
    grounded: bool
    unmatched_tokens: list[str]
    fallback_used: bool
    latency_ms: int
    created_wall_ms: Ms


class Person(BaseModel):
    person_id: str
    scenario_id: str
    kind: Literal["resident", "official", "judge"]
    label: str
    lon: float
    lat: float
    location_source: Literal["assigned", "gps"]
    zone_id: str | None
    status: Literal["unknown", "safe", "evacuating", "needs_help"] = "unknown"
    p_60: float | None = None
    eta_q50_sim_ms: Ms | None = None
    act_by_sim_ms: Ms | None = None  # copied from the person's zone
    alerted_alert_id: str | None = None
    acknowledged: bool = False
    last_seen_wall_ms: Ms
    provenance: Prov


class Report(BaseModel):
    """Public row. The transcript lives in a private table (spec/04 §2.1)."""

    report_id: str
    scenario_id: str
    branch_id: str
    person_id: str | None
    channel: Literal["voice", "tap", "dashboard"]
    kind: Literal["fire", "smoke", "embers", "all_clear"]
    summary: str  # readback text, no raw transcript
    lon: float
    lat: float
    radius_m: float
    trust: float = 0.5
    observed_sim_ms: Ms
    created_wall_ms: Ms
    status: Literal["pending", "accepted", "rejected"] = "pending"
    reject_reason: str | None = None
    observation_id: str | None = None
    provenance: Prov = "injected"


class ReportStatus(BaseModel):
    """Engine -> hub status update, carried in a bundle."""

    report_id: str
    status: Literal["accepted", "rejected"]
    reject_reason: str | None
    observation_id: str | None


class ActivityEvent(BaseModel):
    event_id: str
    scenario_id: str
    branch_id: str
    sim_ms: Ms
    wall_ms: Ms  # engine re-stamps wall_ms at publish; hub assigns seq
    stage: Literal[
        "ingest",
        "observation",
        "filter",
        "forecast",
        "risk",
        "alert",
        "briefing",
        "call",
        "human",
        "report",
        "system",
    ]
    actor: str
    level: Literal["info", "notice", "warn", "error"] = "info"
    message: str  # <= 140 chars, rendered verbatim
    ref_kind: str | None = None
    ref_id: str | None = None
    duration_ms: int | None = None


class Metric(BaseModel):
    key: str
    scenario_id: str
    branch_id: str
    label: str
    value: float | None
    unit: str
    definition: str
    computed_sim_ms: Ms | None  # None = post-hoc evaluation (not gated on sim time)
    detail: dict = Field(default_factory=dict)


class CycleBundle(BaseModel):
    """The unit of publication (engine -> hub; cache -> FileSource)."""

    schema_version: int = SCHEMA_VERSION
    bundle_id: str  # bnd_{scenario}_{branch}_{knowledge_sim_ms}
    scenario_id: str
    branch_id: str
    knowledge_sim_ms: Ms
    cycle: Cycle | None  # None = pre-ignition (scans/observations/sensors/events only)
    zone_risk: list[ZoneRisk]
    observations: list[ObservationRecord]  # new hot pixels + non-satellite obs; never clear pixels
    scan_events: list[ScanEvent]
    sensors: list[SensorInfo]  # status deltas
    alerts: list[Alert]  # new proposals + superseded transitions
    briefings: list[Briefing]  # zero or one per cycle
    people: list[Person]  # risk fields for joined people
    reports: list[ReportStatus]
    metrics: list[Metric]
    events: list[ActivityEvent]


class ClockState(BaseModel):
    """Hub-owned clock. spec/00 §3.2."""

    scenario_id: str
    mode: Literal["replay", "live"]
    playing: bool
    speed: float  # sim seconds per wall second (default 240)
    anchor_sim_ms: Ms
    anchor_wall_ms: Ms
    active_branch_id: str = "main"
    status: Literal["ok", "computing", "catching_up"] = "ok"
    resume_playing: bool = False  # `playing` saved when status went to computing
    pause_on_proposal: bool = True  # hub pauses when a bundle inserts a new proposed alert
    epoch: int  # +1 on seek/reset/branch switch -> clients drop derived caches
    min_sim_ms: Ms
    max_sim_ms: Ms
