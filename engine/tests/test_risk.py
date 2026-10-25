"""Zone risk. spec/03_engine.md §7, §14 test 5 (risk half)."""

import numpy as np

from ember.contracts import Person, ZoneRisk, lead_idx
from ember.risk.risk import eta_quantile, p_at, people, rank, zone_probs, zone_risks

T_REF = 1_736_294_400_000  # 2025-01-08T00:00Z
W = np.array([0.4, 0.3, 0.2, 0.1])
A = np.array([10.0, 50.0, 100.0, np.inf], dtype=np.float32)


def zone(zone_id: str = "Z1", **kw) -> dict:
    props = {"zone_id": zone_id, "name": f"Zone {zone_id}", "population": 500, "housing": 200, "n_critical": 0,
             "tier": 3, "evac_min_default": 45.0, "egress_roads": 3, "approximate": False}
    return props | kw


def run(arr, zones=None, k_min=0, evac=None, prev=None, crit=None, open_r2=None) -> list[ZoneRisk]:
    arr = np.asarray(arr, dtype=np.float32)
    if arr.ndim == 1:
        arr = arr[:, None]
    zones = zones or [zone()]
    return zone_risks(arr, W[: arr.shape[0]] / W[: arr.shape[0]].sum(), T_REF + k_min * 60_000, T_REF, zones,
                      evac or {}, prev, crit or {}, open_r2 or set(), "cyc", "scn", "main")


def risk_row(zone_id: str, status: str, act_by: int | None, score: float) -> ZoneRisk:
    return ZoneRisk(cycle_id="c", scenario_id="s", branch_id="main", zone_id=zone_id, knowledge_sim_ms=0,
                    p_by_lead=[0.0] * 12, eta_q10_sim_ms=None, eta_q50_sim_ms=None, eta_q90_sim_ms=None,
                    act_by_sim_ms=act_by, act_by_basis="", evac_min=45, population=1, housing=1, n_critical=0,
                    tier=3, risk_score=score, rank=0, status=status, trend="flat", drivers=[])


def test_hand_built_four_members():
    assert zone_probs(A, W, 0)[lead_idx(60)] == 0.7
    assert eta_quantile(A, W, 0.3, 0, 360) == 10
    assert eta_quantile(A, W, 0.5, 0, 360) == 50
    assert eta_quantile(A, W, 0.95, 0, 360) is None
    assert eta_quantile(A, W, 0.9, 0, 60) is None  # 100 min is past a 60 min horizon


def test_eta_and_act_by_fields():
    (r,) = run(A)
    assert r.eta_q10_sim_ms == T_REF + 10 * 60_000
    assert r.eta_q50_sim_ms == T_REF + 50 * 60_000
    assert r.eta_q90_sim_ms == T_REF + 100 * 60_000
    assert r.act_by_sim_ms == T_REF + (10 - 45) * 60_000
    assert r.act_by_basis == "q30 arrival − 45 min evac"
    assert p_at(r.p_by_lead, 60) == 0.7


def lead_arr(minutes: float, frac: float) -> np.ndarray:
    """10 equal members, a `frac` share arriving at `minutes`, the rest never."""
    n_hit = round(frac * 10)
    return np.array([minutes] * n_hit + [np.inf] * (10 - n_hit), dtype=np.float32)


def run10(arr) -> ZoneRisk:
    arr = np.asarray(arr, dtype=np.float32)[:, None]
    w = np.full(10, 0.1)
    return zone_risks(arr, w, T_REF, T_REF, [zone()], {}, None, {}, set(), "c", "s", "main")[0]


def test_status_boundaries():
    assert run10(lead_arr(60, 0.5)).status == "order"
    assert run10(lead_arr(60, 0.4)).status == "warning"  # 0.4 by +1h is also >= 0.3 by +2h
    assert run10(lead_arr(120, 0.3)).status == "warning"
    assert run10(lead_arr(120, 0.2)).status == "watch"
    assert run10(lead_arr(360, 0.1)).status == "watch"
    assert run10(lead_arr(361, 0.1)).status == "clear"
    assert run10(lead_arr(500, 0.0)).status == "clear"


def test_open_r2_forces_order():
    (r,) = run(lead_arr(500, 0.0)[:4], open_r2={"Z1"})
    assert r.status == "order"


def test_rank_orders_by_severity_act_by_then_score():
    rows = [
        risk_row("none", "warning", None, 9.0),
        risk_row("late", "warning", 2_000, 1.0),
        risk_row("early_lo", "warning", 1_000, 1.0),
        risk_row("early_hi", "warning", 1_000, 2.0),
        risk_row("order", "order", None, 0.0),
        risk_row("clear", "clear", 0, 5.0),
    ]
    ranked = rank(rows)
    assert [r.zone_id for r in ranked] == ["order", "early_hi", "early_lo", "late", "none", "clear"]
    assert [r.rank for r in ranked] == [1, 2, 3, 4, 5, 6]


def test_trend_boundaries():
    arr = lead_arr(120, 0.5)  # p120 = 0.5
    def trend(prev):
        return zone_risks(arr[:, None], np.full(10, 0.1), T_REF, T_REF, [zone()], {}, prev, {}, set(),
                          "c", "s", "main")[0].trend
    assert trend(None) == "flat"
    assert trend({"other": 0.0}) == "flat"
    assert trend({"Z1": 0.45}) == "flat"
    assert trend({"Z1": 0.55}) == "flat"
    assert trend({"Z1": 0.44}) == "up"
    assert trend({"Z1": 0.56}) == "down"


def test_drivers_are_types_never_names():
    z = zone(n_critical=2, egress_roads=1)
    (r,) = run(A, zones=[z], crit={"Z1": ["nursing home", "school"]})
    assert r.drivers == ["P 90% by +2h", "nursing home", "school"]
    (r,) = run(A, zones=[zone(egress_roads=1)], crit={"Z1": ["school"]})
    assert r.drivers == ["P 90% by +2h", "school", "1 egress road"]
    assert not any("Sunrise" in d for d in r.drivers)
    assert r.population == 500 and r.housing == 200 and r.tier == 3


def test_score_formula():
    (r,) = run(A, zones=[zone(population=999, tier=1)])
    assert r.risk_score == round(0.9 * (1 + np.log10(1000)) * 2.0, 6)


def test_people_returns_only_changed():
    (r,) = run(A)
    same = Person(person_id="per_1", scenario_id="scn", kind="resident", label="a", lon=0, lat=0,
                  location_source="assigned", zone_id="Z1", p_60=0.7, eta_q50_sim_ms=r.eta_q50_sim_ms,
                  act_by_sim_ms=r.act_by_sim_ms, last_seen_wall_ms=0, provenance="simulated")
    stale = same.model_copy(update={"person_id": "per_2", "p_60": None})
    elsewhere = same.model_copy(update={"person_id": "per_3", "zone_id": "Z9", "p_60": None})
    nozone = same.model_copy(update={"person_id": "per_4", "zone_id": None, "p_60": None})
    out = people([same, stale, elsewhere, nozone], [r])
    assert [p.person_id for p in out] == ["per_2"]
    assert (out[0].p_60, out[0].eta_q50_sim_ms, out[0].act_by_sim_ms) == (0.7, r.eta_q50_sim_ms, r.act_by_sim_ms)


def test_operator_evac_edit_shifts_act_by_exactly():
    (a,) = run(A, evac={"Z1": 45})
    (b,) = run(A, evac={"Z1": 90})
    assert a.act_by_sim_ms - b.act_by_sim_ms == 45 * 60_000
    assert b.act_by_basis == "q30 arrival − 90 min evac"
    assert b.evac_min == 90
