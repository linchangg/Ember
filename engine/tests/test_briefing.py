"""Claude briefing writer. spec/05_ai_voice.md §8 test 2 (live smoke) plus offline checks."""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from emberline.ai.briefing import draft, fixture_input, input_hash

INPUTS = Path(__file__).parent / "data" / "briefing_inputs"
ZONE_KEYS = {
    "zone_id", "name", "p_60", "p_120", "eta_q50_pst", "act_by_pst",
    "evac_min", "population", "critical", "egress_roads", "trend",
}  # fmt: skip

needs_live = pytest.mark.skipif(
    not (os.environ.get("EMBERLINE_LIVE_TESTS") and os.environ.get("ANTHROPIC_API_KEY")),
    reason="live Claude",
)


def fake_client(payload: dict | None = None, exc: Exception | None = None):
    async def create(**kwargs):
        assert not {"temperature", "top_p", "top_k", "tool_choice"} & set(kwargs)
        if exc:
            raise exc
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=json.dumps(payload))],
        )

    return SimpleNamespace(messages=SimpleNamespace(create=create))


def good_output(inp: dict) -> dict:
    z = inp["zones"][0]
    return {
        "headline": f"Warning for {z['zone_id']}: act by {z['act_by_pst']} PST",
        "bullets": [f"About {z['population']} residents; {z['egress_roads']} egress roads."],
        "text": f"DEMO REPLAY (Jan 2025). Warning for {z['zone_id']}. Act by {z['act_by_pst']} PST. Forecast {inp['issued_pst']}.",
        "voice_script": "This is an Emberline demo replay of January 2025. A warning is recommended. You make the call.",
    }


def test_build_input_shape() -> None:
    inp, alert, _ = fixture_input(20)
    assert set(inp) == {
        "issued_pst", "data_through_pst", "forecast_age_min", "alert", "zones", "fire", "sensors", "honesty",
    }  # fmt: skip
    assert [z["zone_id"] for z in inp["zones"]] == alert.zone_ids
    for z in inp["zones"]:
        assert set(z) == ZONE_KEYS
        assert all(":" not in c and "Fixture" not in c for c in z["critical"])  # types only, never names
    assert len(json.dumps(inp)) <= 4096


def test_saved_inputs_match_fixture() -> None:
    for n in (18, 20, 30):
        saved = json.loads((INPUTS / f"c{n}.json").read_text(encoding="utf-8"))
        assert saved == fixture_input(n)[0], f"c{n}.json is stale; regenerate it"


def test_input_hash_stable_across_key_order() -> None:
    inp = fixture_input(20)[0]
    assert input_hash(inp) == input_hash(dict(reversed(list(inp.items()))))
    assert len(input_hash(inp)) == 64


def test_draft_valid_output_is_grounded() -> None:
    inp, alert, cycle = fixture_input(20)
    brf = asyncio.run(draft(inp, alert, cycle, client=fake_client(good_output(inp))))
    assert brf.grounded and not brf.fallback_used, brf.unmatched_tokens
    assert brf.briefing_id == f"brf_{alert.alert_id}" and brf.input_hash == input_hash(inp)


def test_draft_wrong_time_falls_back_to_template() -> None:
    inp, alert, cycle = fixture_input(20)
    out = good_output(inp)
    out["text"] = out["text"].replace(inp["zones"][0]["act_by_pst"], "23:59")
    brf = asyncio.run(draft(inp, alert, cycle, client=fake_client(out)))
    assert brf.fallback_used and not brf.grounded
    assert "time 23:59" in brf.unmatched_tokens
    assert brf.text == alert.text and brf.voice_script == alert.voice_script and brf.bullets == []


def test_draft_client_error_never_raises() -> None:
    inp, alert, cycle = fixture_input(20)
    brf = asyncio.run(draft(inp, alert, cycle, client=fake_client(exc=RuntimeError("boom"))))
    assert brf.fallback_used and brf.unmatched_tokens == ["error: RuntimeError"]
    assert brf.text == alert.text


@needs_live
def test_live_smoke() -> None:
    results = []
    for n in (18, 20, 30):
        inp, alert, cycle = fixture_input(n)
        results.append((n, asyncio.run(draft(inp, alert, cycle))))
    for n, brf in results:
        if not brf.grounded:
            print(f"c{n} FALLBACK: {brf.unmatched_tokens}")
    assert sum(brf.grounded for _, brf in results) >= 2
