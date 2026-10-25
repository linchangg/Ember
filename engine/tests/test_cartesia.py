"""T11: Cartesia outbound call. No real calls: every HTTP exchange goes through httpx.MockTransport."""

import asyncio
import json
import re

import httpx
import pytest

from ember.ai.briefing import fixture_bundle
from ember.notify import cartesia
from ember.risk import alerts

TO = "+15555550100"
ENV = {
    "CARTESIA_API_KEY": "sk_car_test",
    "CARTESIA_AGENT_ID": "agent_test",
    "CARTESIA_FROM_NUMBER_ID": "pn_test",
    "CARTESIA_VERSION": "2026-08-14",
}


@pytest.fixture
def alert():
    return next(a for a in fixture_bundle(20).alerts if a.state == "proposed")


@pytest.fixture
def env(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _call(alert, handler, **kw) -> cartesia.CallResult:
    async def go():
        async with _client(handler) as c:
            return await cartesia.place_call(alert, kw.pop("to", TO), client=c, **kw)
    return asyncio.run(go())


def test_build_request_shape(alert, env) -> None:
    body = cartesia.build_request(alert, TO)
    assert set(body) == {"agent_id", "from_number_id", "outbound_calls"}
    assert body["agent_id"] == "agent_test" and body["from_number_id"] == "pn_test"
    (call,) = body["outbound_calls"]
    assert call["to_number"] == TO
    dv = call["dynamic_variables"]
    assert set(dv) == {"alert_id", "voice_script", "zones_spoken", "act_by_spoken", "p_words", "issued_spoken"}
    assert all(isinstance(v, str) for v in dv.values())
    assert all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", k) for k in dv)  # Cartesia variable-name rule
    assert len(json.dumps(dv).encode("utf-8")) <= 16 * 1024


def test_dynamic_variables_spoken_pst_and_p_words(alert) -> None:
    dv = cartesia.dynamic_variables(alert)
    assert dv["alert_id"] == alert.alert_id and dv["voice_script"] == alert.voice_script
    assert re.fullmatch(r"\d{1,2}:\d{2} [AP]M", dv["act_by_spoken"])
    assert re.fullmatch(r"\d{1,2}:\d{2} [AP]M", dv["issued_spoken"])
    assert f"Act by {dv['act_by_spoken']}." in alert.voice_script  # same spoken PST time as the template
    assert f"Fire is {dv['p_words']} to reach" in alert.voice_script
    assert dv["zones_spoken"] in alert.voice_script  # zone name, not the ID
    # 1736308500000 ms = 2025-01-08T03:55Z = 7:55 PM PST
    assert cartesia._spoken_time(1736308500000, cartesia.ZoneInfo("America/Los_Angeles")) == "7:55 PM"


@pytest.mark.parametrize("p", [0.296, 0.495, 0.695, 0.895, 0.9])
def test_p_words_matches_template_near_thresholds(alert, p) -> None:
    text, voice = alerts._render("warning", "FIX-Z1", "Fixture Z1", [], 10, p, 1, alert.act_by_sim_ms,
                                 alert.triggered_sim_ms, alert.triggered_sim_ms)
    dv = cartesia.dynamic_variables(alert.model_copy(update={"text": text, "voice_script": voice}))
    assert dv["p_words"] == alerts.p_words(p)


def test_zones_spoken_matches_voice_script(alert) -> None:
    ids = [f"FIX-Z{i}" for i in range(1, 5)]
    names = [cartesia._zone_names(alert.scenario_id).get(z, z) for z in ids]
    for spoken in (alerts._spoken_list(names), alerts._spoken_list(names[:3] + ["1 more zones"])):
        _, voice = alerts._render("warning", ", ".join(ids), spoken, [], 10, 0.6, 1, alert.act_by_sim_ms,
                                  alert.triggered_sim_ms, alert.triggered_sim_ms)
        dv = cartesia.dynamic_variables(alert.model_copy(update={"zone_ids": ids, "voice_script": voice}))
        assert dv["zones_spoken"] == spoken


def test_unconfigured(alert, monkeypatch) -> None:
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    assert not cartesia.configured()
    r = _call(alert, lambda req: pytest.fail("must not call out"))
    assert (r.ok, r.call_status) == (False, "unconfigured")


def test_dry_run_makes_no_request(alert, env) -> None:
    r = _call(alert, lambda req: pytest.fail("must not call out"), dry_run=True)
    assert (r.ok, r.call_status, r.call_id) == (False, "dry_run", None)


def test_success(alert, env) -> None:
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["req"] = req
        return httpx.Response(200, json={"calls": [{"number": TO, "agent_call_id": "ac_123"}]})

    r = _call(alert, handler)
    assert (r.ok, r.call_id, r.call_status, r.error) == (True, "ac_123", "placed", None)
    req = seen["req"]
    assert req.method == "POST" and str(req.url) == "https://api.cartesia.ai/agents/calls"
    assert req.headers["Authorization"] == "Bearer sk_car_test"
    assert req.headers["Cartesia-Version"] == "2026-08-14"
    assert json.loads(req.content) == cartesia.build_request(alert, TO)


def test_200_with_per_call_error(alert, env) -> None:
    body = {"calls": [{"number": TO, "error": {"message": f"cannot dial {TO}"}}]}
    r = _call(alert, lambda req: httpx.Response(200, json=body))
    assert (r.ok, r.call_status) == (False, "error")
    assert "cannot dial" in r.error and TO not in r.error and "0100" in r.error


def test_4xx(alert, env) -> None:
    r = _call(alert, lambda req: httpx.Response(404, json={"error": "deployment not found"}))
    assert (r.ok, r.call_status) == (False, "error")
    assert r.error.startswith("HTTP 404") and "deployment not found" in r.error


def test_network_error(alert, env) -> None:
    def handler(req):
        raise httpx.ConnectError("no route", request=req)

    r = _call(alert, handler)
    assert (r.ok, r.call_status) == (False, "error") and "ConnectError" in r.error


@pytest.mark.parametrize("handler", [
    lambda req: httpx.Response(200, text="not json"),
    lambda req: httpx.Response(200, json={"calls": []}),
    lambda req: httpx.Response(200, json={"unexpected": 1}),
    lambda req: (_ for _ in ()).throw(RuntimeError("boom")),
])
def test_place_call_never_raises(alert, env, handler) -> None:
    r = _call(alert, handler)
    assert r.ok is False and r.call_status == "error"


def test_bad_number_never_raises(alert, env) -> None:
    for bad in ("5555550100", "", "+0123", "+1555abc0100"):
        r = _call(alert, lambda req: pytest.fail("must not call out"), to=bad)
        assert (r.ok, r.call_status) == (False, "error")


def test_mask() -> None:
    assert cartesia.mask("+15555554821") == "•••• 4821"
    body = cartesia.masked_request({"outbound_calls": [{"to_number": TO}]})
    assert body["outbound_calls"][0]["to_number"] == "•••• 0100"


def test_cli_dry_run_masks_number(monkeypatch, capsys) -> None:
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("ember.ai.briefing.load_env", lambda *a: None)  # keep engine/.env out of the test
    monkeypatch.setattr("sys.argv", ["cartesia", "--dry-run", "--fixture-alert", "20", "--to", TO])
    cartesia.main()
    out = capsys.readouterr().out
    assert TO not in out and "•••• 0100" in out and "dry_run" in out
    assert json.loads(out[out.index("{"):out.index("\nCallResult")])["outbound_calls"][0]["dynamic_variables"]
