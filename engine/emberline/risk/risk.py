"""Zone risk from ensemble arrival times. spec/03_engine.md §7. Pure functions, no I/O."""

import math

import numpy as np

from emberline.contracts import LEADS_MIN, Person, ZoneRisk, lead_idx

SEVERITY = {"order": 3, "warning": 2, "watch": 1, "clear": 0}
TIER_MULT = {1: 2.0, 2: 1.3, 3: 1.0}


def p_at(p_by_lead: list[float], lead_min: int) -> float:
    return p_by_lead[lead_idx(lead_min)]


def zone_probs(arr_min: np.ndarray, w: np.ndarray, k_min: float) -> list[float]:
    """P(A <= k_min + lead) for each of the 12 leads, weighted, rounded to 6 decimals."""
    a = np.asarray(arr_min, dtype=np.float64)
    w = np.asarray(w, dtype=np.float64)
    return [round(float(w[a <= k_min + lead].sum()), 6) for lead in LEADS_MIN]


def eta_quantile(arr_min: np.ndarray, w: np.ndarray, q: float, k_min: float, horizon_min: int) -> float | None:
    """First sorted arrival whose cumulative weight reaches q; None if never or past the horizon."""
    a = np.asarray(arr_min, dtype=np.float64)
    order = np.argsort(a, kind="stable")
    cum = np.cumsum(np.asarray(w, dtype=np.float64)[order])
    hits = np.nonzero(cum >= q - 1e-9)[0]
    if hits.size == 0:
        return None
    t = float(a[order[hits[0]]])
    if not math.isfinite(t) or t > k_min + horizon_min:
        return None
    return t


def _to_ms(t_min: float | None, t_ref_ms: int) -> int | None:
    return None if t_min is None else t_ref_ms + int(round(t_min * 60_000))


def _status(p: list[float], open_order: bool) -> str:
    if open_order or p_at(p, 60) >= 0.5:
        return "order"
    if p_at(p, 120) >= 0.3:
        return "warning"
    if p_at(p, 360) >= 0.1:
        return "watch"
    return "clear"


def _trend(zone_id: str, p120: float, prev_p120: dict[str, float] | None) -> str:
    if prev_p120 is None or zone_id not in prev_p120:
        return "flat"
    delta = round(p120 - prev_p120[zone_id], 6)  # p's are 6-decimal; keeps ±0.05 exact
    return "up" if delta > 0.05 else "down" if delta < -0.05 else "flat"


def _drivers(p120: float, types: list[str], egress_roads: int) -> list[str]:
    out = [f"P {p120 * 100:.0f}% by +2h", *types]
    if egress_roads <= 1:
        out.append(f"{egress_roads} egress road")
    return out[:3]


def zone_risks(
    zone_arr: np.ndarray,
    weights: np.ndarray,
    k_ms: int,
    t_ref_ms: int,
    zones: list[dict],
    evac_min: dict[str, float],
    prev_p120: dict[str, float] | None,
    critical_types: dict[str, list[str]],
    open_r2_zones: set[str],
    cycle_id: str,
    scenario_id: str,
    branch_id: str,
    horizon_min: int = 360,
    act_by_q: float = 0.3,
) -> list[ZoneRisk]:
    """One ranked ZoneRisk per zone. zone_arr is (N, Z) minutes since t_ref, columns in `zones` order."""
    assert zone_arr.shape == (len(weights), len(zones)), f"zone_arr {zone_arr.shape} vs N={len(weights)}, Z={len(zones)}"
    k_min = (k_ms - t_ref_ms) / 60_000
    q_pct = round(act_by_q * 100)
    rows = []
    for j, zone in enumerate(zones):
        zid = zone["zone_id"]
        arr = zone_arr[:, j]
        p = zone_probs(arr, weights, k_min)
        p120 = p_at(p, 120)
        eta = {q: _to_ms(eta_quantile(arr, weights, q, k_min, horizon_min), t_ref_ms) for q in (0.1, 0.5, 0.9, act_by_q)}
        evac = float(evac_min.get(zid, zone["evac_min_default"]))
        act_by = None if eta[act_by_q] is None else eta[act_by_q] - int(round(evac * 60_000))
        score = p120 * (1 + math.log10(1 + zone["population"])) * TIER_MULT[zone["tier"]]
        rows.append(
            ZoneRisk(
                cycle_id=cycle_id,
                scenario_id=scenario_id,
                branch_id=branch_id,
                zone_id=zid,
                knowledge_sim_ms=k_ms,
                p_by_lead=p,
                eta_q10_sim_ms=eta[0.1],
                eta_q50_sim_ms=eta[0.5],
                eta_q90_sim_ms=eta[0.9],
                act_by_sim_ms=act_by,
                act_by_basis=f"q{q_pct} arrival − {evac:g} min evac",
                evac_min=evac,
                population=zone["population"],
                housing=zone["housing"],
                n_critical=zone["n_critical"],
                tier=zone["tier"],
                risk_score=round(score, 6),
                rank=0,
                status=_status(p, zid in open_r2_zones),
                trend=_trend(zid, p120, prev_p120),
                drivers=_drivers(p120, critical_types.get(zid, []), zone["egress_roads"]),
            )
        )
    return rank(rows)


def rank(risks: list[ZoneRisk]) -> list[ZoneRisk]:
    """Severity desc, act-by asc (None last), score desc. Returns copies with rank from 1."""
    ordered = sorted(
        risks,
        key=lambda r: (
            -SEVERITY[r.status],
            r.act_by_sim_ms is None,
            r.act_by_sim_ms or 0,
            -r.risk_score,
        ),
    )
    return [r.model_copy(update={"rank": i}) for i, r in enumerate(ordered, start=1)]


def apply_open_orders(risks: list[ZoneRisk], open_r2_zones: set[str]) -> list[ZoneRisk]:
    patched = [r.model_copy(update={"status": "order"}) if r.zone_id in open_r2_zones else r for r in risks]
    return rank(patched)


def people(persons: list[Person], risks: list[ZoneRisk]) -> list[Person]:
    """Copy each person's zone values onto them; return only persons whose values changed."""
    by_zone = {r.zone_id: r for r in risks}
    changed = []
    for person in persons:
        risk = by_zone.get(person.zone_id) if person.zone_id else None
        if risk is None:
            continue
        values = {
            "p_60": p_at(risk.p_by_lead, 60),
            "eta_q50_sim_ms": risk.eta_q50_sim_ms,
            "act_by_sim_ms": risk.act_by_sim_ms,
        }
        if any(getattr(person, key) != val for key, val in values.items()):
            changed.append(person.model_copy(update=values))
    return changed
