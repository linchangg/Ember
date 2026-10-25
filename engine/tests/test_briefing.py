"""Claude briefing writer. spec/05_ai_voice.md §8 test 2 (live smoke) plus offline checks."""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from emberline.ai.briefing import draft, fixture_input, input_hash, load_env, make_client

INPUTS = Path(__file__).parent / "data" / "briefing_inputs"
ZONE_KEYS = {
    "zone_id", "name", "p_60", "p_120", "eta_q50_pst", "act_by_pst",
    "evac_min", "population", "critical", "egress_roads", "trend",
}  # fmt: skip

def _live_ready() -> bool:
    if not os.environ.get("EMBERLINE_LIVE_TESTS"):
        return False
    load_env()
    return bool(os.environ.get("OPENROUTER_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))


needs_live = pytest.mark.skipif(not _live_ready(), reason="live Claude")


def fake_client(payload: dict | None = None, exc: Exception | None = None):
    async def create(**kwargs):
        calls.append(kwargs)
        assert not {"temperature", "top_p", "top_k", "tool_choice"} & set(kwargs)
        if exc:
            raise exc
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=json.dumps(payload))],
        )

    calls: list[dict] = []
    return SimpleNamespace(messages=SimpleNamespace(create=create), calls=calls)


def good_output(inp: dict) -> dict:
    z = inp["zones"][0]
    return {
        "headline": f"Warning for {z['zone_id']}: act by {z['act_by_pst']} PST",
        "bullets": [f"About {z['population']} residents; {z['egress_roads']} egress roads."],
        "text": f"Warning for {z['zone_id']}. Act by {z['act_by_pst']} PST. Forecast {inp['issued_pst']}.",
        "voice_script": "This is Emberline. A warning is recommended. You make the call.",
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


def test_model_from_env_is_sent_and_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    inp, alert, cycle = fixture_input(20)
    monkeypatch.setenv("EMBERLINE_BRIEFING_MODEL", "anthropic/claude-sonnet-5.5")
    client = fake_client(good_output(inp))
    brf = asyncio.run(draft(inp, alert, cycle, client=client))
    assert client.calls[0]["model"] == brf.model == "anthropic/claude-sonnet-5.5"
    monkeypatch.delenv("EMBERLINE_BRIEFING_MODEL")
    assert asyncio.run(draft(inp, alert, cycle, client=fake_client(good_output(inp)))).model == "claude-sonnet-5-5"


def test_make_client_prefers_openrouter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://openrouter.ai/api")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = make_client()
    assert c.auth_token == "sk-or-test" and c.api_key is None and str(c.base_url).startswith("https://openrouter.ai/api")
    assert c.max_retries == 0
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")  # Claude Code's shell sets this
    assert str(make_client().base_url).startswith("https://openrouter.ai/api")  # key never goes elsewhere
    monkeypatch.delenv("OPENROUTER_API_KEY")
    monkeypatch.delenv("ANTHROPIC_BASE_URL")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    c = make_client()
    assert c.api_key == "sk-ant-test" and c.auth_token is None


@needs_live
def test_live_smoke() -> None:
    results = []
    for n in (18, 20, 30):
        inp, alert, cycle = fixture_input(n)
        results.append((n, asyncio.run(draft(inp, alert, cycle))))
    for n, brf in results:
        print(f"c{n} {brf.model} {'GROUNDED' if brf.grounded else 'FALLBACK'}: {brf.unmatched_tokens}")
        print(f"  headline: {brf.headline}\n  bullets: {brf.bullets}\n  text: {brf.text}\n  voice: {brf.voice_script}")
    assert sum(brf.grounded for _, brf in results) >= 2
