"""call_cards: precomputed duty-officer call cards from synthetic bundles (no engine, no network)."""

import json
import re

from ember.ai.briefing import fixture_bundle
from ember.contracts import Briefing
from ember.notify import cartesia
from ember.scripts import call_cards

ENV = {"CARTESIA_API_KEY": "sk_car_SECRET", "CARTESIA_AGENT_ID": "agent_SECRET",
       "CARTESIA_FROM_NUMBER_ID": "pn_SECRET", "CARTESIA_VERSION": "2026-08-14",
       "DUTY_OFFICER_NUMBERS": "+15555550100"}
BASE = next(a for a in fixture_bundle(20).alerts if a.state == "proposed")


def _alert(aid: str, t: int, state: str = "proposed", **kw):
    return BASE.model_copy(update={"alert_id": aid, "triggered_sim_ms": t, "state": state, **kw})


def _briefing(aid: str, grounded: bool) -> Briefing:
    return Briefing(briefing_id=f"br_{aid}", scenario_id="fixture", branch_id="main", cycle_id="c", alert_id=aid,
                    model="m", prompt_version="v", input_hash="x", headline=f"h {aid}", bullets=["b"], text="t",
                    voice_script="v", grounded=grounded, unmatched_tokens=[] if grounded else ["9"],
                    fallback_used=not grounded, latency_ms=1, created_wall_ms=1)


def _write(root, cycles: list[tuple[int, list, list]]) -> None:
    d = root / "cache" / "fixture" / "main"
    entries = []
    for k, alerts, briefings in cycles:
        (d / str(k)).mkdir(parents=True)
        (d / str(k) / "bundle.json").write_text(json.dumps({
            "alerts": [a.model_dump(mode="json") for a in alerts],
            "briefings": [b.model_dump(mode="json") for b in briefings]}), encoding="utf-8")
        entries.append({"knowledge_sim_ms": k, "bundle": f"cache/fixture/main/{k}/bundle.json"})
    (d / "index.json").write_text(json.dumps({"cycles": entries}), encoding="utf-8")


def test_trigger_matches_poller() -> None:
    # sync/poller.py _on_alert_approved: calls iff state == "approved" and decided_by
    assert call_cards.would_call(_alert("a", 1, "approved", decided_by="op"))
    assert not call_cards.would_call(_alert("a", 1, "approved", decided_by=None))
    assert not call_cards.would_call(_alert("a", 1, "proposed"))
    assert call_cards.callable_alert(_alert("a", 1, "proposed"))
    for state in ("declined", "superseded", "failed"):
        assert not call_cards.callable_alert(_alert("a", 1, state))


def test_one_card_per_alert_ordered_and_clean(tmp_path, monkeypatch) -> None:
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    t0 = BASE.triggered_sim_ms
    late, early, gone = _alert("al_late", t0 + 600_000), _alert("al_early", t0), _alert("al_gone", t0, "declined")
    brief = [_briefing("al_early", grounded=True), _briefing("al_late", grounded=False)]  # unvalidated: dropped
    _write(tmp_path, [
        (t0, [late, early, gone], brief),
        (t0 + 300_000, [early.model_copy(update={"state": "superseded"})], []),  # re-seen: still one card
    ])
    cards = call_cards.branch_cards(tmp_path, "fixture", "main")
    assert [c["alert_id"] for c in cards] == ["al_early", "al_late"]
    assert [c["call_id"] for c in cards] == ["call_al_early", "call_al_late"]
    c = cards[0]
    assert c["dynamic_variables"] == cartesia.dynamic_variables(early)
    assert c["request"]["body"]["outbound_calls"][0]["dynamic_variables"] == c["dynamic_variables"]
    assert c["transcript"][0]["text"] == early.voice_script
    assert c["briefing"]["headline"] == "h al_early" and cards[1]["briefing"] is None
    out = json.dumps(cards)
    assert not any(v in out for v in ENV.values() if v != "2026-08-14")
    assert not re.search(r"\+\d{7,15}", out)


def test_missing_branch_is_none(tmp_path) -> None:
    assert call_cards.branch_cards(tmp_path, "fixture", "openloop") is None
