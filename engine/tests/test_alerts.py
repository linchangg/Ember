"""Frozen alert rules. spec/03_engine.md §8, §14 test 4; fixture cross-check against make_fixture.propose()."""

import json
from pathlib import Path

from emberline.contracts import Alert, CycleBundle, ZoneRisk, lead_idx
from emberline.risk.alerts import evaluate, load_rules, p_words, template

REPO = Path(__file__).resolve().parents[2]
MAIN = REPO / "fixtures" / "cache" / "fixture" / "main"
STATIC = REPO / "fixtures" / "static" / "fixture"
RULES = load_rules()
K0 = 1_736_301_600_000
MIN = 60_000


def risk(zone_id: str, p60: float = 0.0, p120: float = 0.0, act_by: int | None = None, rank: int = 1,
         pop: int = 500) -> ZoneRisk:
    p = [0.0] * 12
    p[lead_idx(60)] = p60
    for lead in range(120, 361, 30):
        p[lead_idx(lead)] = p120
    return ZoneRisk(cycle_id="c", scenario_id="s", branch_id="main", zone_id=zone_id, knowledge_sim_ms=0,
                    p_by_lead=p, eta_q10_sim_ms=None, eta_q50_sim_ms=None, eta_q90_sim_ms=None,
                    act_by_sim_ms=act_by, act_by_basis="q30 arrival − 45 min evac", evac_min=45, population=pop,
                    housing=1, n_critical=0, tier=3, risk_score=p120, rank=rank, status="warning", trend="flat",
                    drivers=[])


def run(risks: list[ZoneRisk], hub: list[Alert], k: int):
    return evaluate(risks, hub, k, f"cyc_s_main_{k}", "s", "main", RULES, {}, {}, k - 5 * MIN)


def keys(alerts: list[Alert]) -> list[tuple[str, str, str]]:
    return [(a.rule_id, a.zone_ids[0], a.state) for a in alerts]


def test_rules_file_matches_contract():
    assert RULES["version"] == 1 and RULES["cooldown_min"] == 30 and RULES["merge_min_zones"] == 3
    assert [r["id"] for r in RULES["rules"]] == ["R1_WARNING", "R2_ORDER"]


def test_p_words():
    assert [p_words(p) for p in (0.95, 0.9, 0.7, 0.5, 0.3, 0.29)] == [
        "very likely", "very likely", "likely", "more likely than not", "possible", "unlikely"]


def sequence(warning_state: str):
    first = run([risk("Z", p120=0.35)], [], K0)
    assert keys(first.all) == [("R1_WARNING", "Z", "proposed")]
    warning = first.new[0]
    assert warning.alert_id == f"al_main_Z_R1_WARNING_{K0}" and warning.level == "warning"
    assert warning.draft_source == "template" and warning.rule_text == RULES["rules"][0]["text"]
    assert run([risk("Z", p120=0.4)], [warning], K0 + 10 * MIN).all == []
    hub = [warning.model_copy(update={"state": warning_state})]
    return run([risk("Z", p60=0.6, p120=0.8)], hub, K0 + 40 * MIN)


def test_r1_then_cooldown_then_r2_supersedes_proposed_warning():
    later = sequence("proposed")
    assert keys(later.new) == [("R2_ORDER", "Z", "proposed")]
    assert later.new[0].level == "order_recommended"
    assert keys(later.superseded) == [("R1_WARNING", "Z", "superseded")]
    assert later.superseded[0].alert_id == f"al_main_Z_R1_WARNING_{K0}"


def test_approved_warning_is_never_superseded():
    later = sequence("approved")
    assert keys(later.new) == [("R2_ORDER", "Z", "proposed")]
    assert later.superseded == []


def test_cooldown_counts_any_state():
    declined = run([risk("Z", p120=0.35)], [], K0).new[0].model_copy(update={"state": "declined"})
    assert run([risk("Z", p120=0.35)], [declined], K0 + 29 * MIN).all == []
    assert keys(run([risk("Z", p120=0.35)], [declined], K0 + 30 * MIN).new) == [("R1_WARNING", "Z", "proposed")]


def test_same_cycle_order_wins_over_warning():
    out = run([risk("Z", p60=0.6, p120=0.8)], [], K0)
    assert keys(out.all) == [("R2_ORDER", "Z", "proposed")]


def test_merge_three_zones_and_cooldown_covers_all():
    risks = [risk("B", p120=0.4, rank=2), risk("A", p120=0.5, rank=1), risk("C", p120=0.3, rank=3)]
    out = run(risks, [], K0)
    assert len(out.new) == 1
    assert out.new[0].zone_ids == ["A", "B", "C"]
    assert out.new[0].alert_id == f"al_main_A_R1_WARNING_{K0}"
    assert "(P 50%)" in out.new[0].text
    declined = out.new[0].model_copy(update={"state": "declined"})
    assert run(risks, [declined], K0 + 10 * MIN).all == []
    assert run(risks, out.new, K0 + 60 * MIN).all == []  # still proposed -> active


def test_two_zones_do_not_merge():
    out = run([risk("A", p120=0.5, rank=1), risk("B", p120=0.4, rank=2)], [], K0)
    assert [a.zone_ids for a in out.new] == [["A"], ["B"]]


def test_act_by_within_negative_fires_r2():
    out = run([risk("Z", act_by=K0 - 5 * MIN)], [], K0)
    assert keys(out.new) == [("R2_ORDER", "Z", "proposed")]
    assert out.new[0].act_by_sim_ms == K0 - 5 * MIN
    assert run([risk("Z", act_by=K0 + 16 * MIN)], [], K0).all == []
    assert keys(run([risk("Z", act_by=K0 + 15 * MIN)], [], K0).new) == [("R2_ORDER", "Z", "proposed")]


def test_min_population_skips_zone():
    assert run([risk("Z", p120=0.9, pop=0)], [], K0).all == []


def test_template_six_zone_merge_fits():
    ids = [f"ALD-FARNSWORTH-{i}" for i in range(6)]
    zones = [risk(z, p60=0.7, rank=i + 1) for i, z in enumerate(ids)]
    names = {z: f"Altadena Farnsworth Park North {i}" for i, z in enumerate(ids)}
    crit = {ids[0]: ["nursing home"], ids[1]: ["school", "nursing home"]}
    text, voice = template("order_recommended", zones, names, crit, 0.7, 1, K0 + 30 * MIN, K0, K0 - 5 * MIN)
    assert "demo" not in text.lower()
    assert voice.startswith("This is Emberline.")
    assert len(text) <= 300 and len(voice.split()) <= 110
    assert "EVACUATION ORDER recommended" in text and "(P 70%)" in text
    assert ", nursing home, school." in text and "including a nursing home and a school" in voice
    assert "~3,000 residents" in text and "About 3000 residents" in voice


def test_template_times_and_unknown_act_by():
    k = 1_736_303_400_000  # 2025-01-08 02:30Z = 18:30 PST on Jan 7
    text, voice = template("warning", [risk("Z")], {"Z": "Zed"}, {}, 0.35, 2, None, k, k - 10 * MIN)
    assert "Act by unknown PST" in text and "Act by an unknown time." in voice
    assert "Forecast 18:30, satellite data through 18:20." in text
    assert "Recommend a warning for Zed. Fire is possible to reach the zone within 2 hours." in voice
    text, voice = template("warning", [risk("Z")], {"Z": "Zed"}, {}, 0.35, 2, k + 45 * MIN, k, k)
    assert "Act by 19:15 PST" in text and "Act by 7:15 PM." in voice


def test_fixture_cross_check():
    """evaluate() over the committed zone_risk rows reproduces the committed alerts' (cycle, rule, zone, state,
    act_by_sim_ms). Text is not compared: make_fixture's alert_copy words critical assets differently."""
    zones = json.loads((STATIC / "zones.geojson").read_text(encoding="utf-8"))["features"]
    names = {f["properties"]["zone_id"]: f["properties"]["name"] for f in zones}
    crit: dict[str, list[str]] = {}
    for f in json.loads((STATIC / "assets.geojson").read_text(encoding="utf-8"))["features"]:
        crit.setdefault(f["properties"]["zone_id"], []).append(f["properties"]["type"])
    index = json.loads((MAIN / "index.json").read_text(encoding="utf-8"))
    bundles = [CycleBundle.model_validate_json((REPO / "fixtures" / c["bundle"]).read_text(encoding="utf-8"))
               for c in index["cycles"]]
    cycles = [b for b in bundles if b.cycle is not None]
    assert len(bundles) - len(cycles) == 3

    hub: dict[str, Alert] = {}
    ours, theirs = [], []
    for n, b in enumerate(cycles, start=1):
        out = evaluate(b.zone_risk, list(hub.values()), b.knowledge_sim_ms, b.cycle.cycle_id, b.scenario_id,
                       b.branch_id, RULES, names, crit, b.cycle.data_through_sim_ms)
        for a in out.all:
            hub[a.alert_id] = a
            ours.append((n, a.rule_id, a.zone_ids[0], a.state, a.act_by_sim_ms))
        theirs += [(n, a.rule_id, a.zone_ids[0], a.state, a.act_by_sim_ms) for a in b.alerts]

    expected = [
        (18, "R1_WARNING", "FIX-Z3", "proposed"),
        (20, "R1_WARNING", "FIX-Z2", "proposed"),
        (30, "R2_ORDER", "FIX-Z3", "proposed"),
        (30, "R1_WARNING", "FIX-Z3", "superseded"),
        (32, "R2_ORDER", "FIX-Z2", "proposed"),
        (32, "R1_WARNING", "FIX-Z2", "superseded"),
    ]
    assert [t[:4] for t in theirs] == expected
    assert ours == theirs
    for a in hub.values():
        assert len(a.text) <= 300 and len(a.voice_script.split()) <= 110
        assert "Fixture school" not in a.text + a.voice_script
