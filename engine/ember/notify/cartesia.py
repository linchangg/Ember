"""Cartesia outbound call to the duty officer. spec/05_ai_voice.md §4 "Placing the call", WAVE2 §1.1.

Request shape verified Sat Oct 24 2026 against
https://docs.cartesia.ai/api-reference/agents/calls/create-outbound-call (Cartesia-Version 2026-08-14) and
https://docs.cartesia.ai/line/integrations/telephony/outbound-dialing :
  POST https://api.cartesia.ai/agents/calls, headers `Authorization: Bearer <key>` + required `Cartesia-Version`,
  body {agent_id, from_number_id, outbound_calls: [{to_number (E.164), dynamic_variables}]}.
  A 200 answers per destination: calls[i] carries `agent_call_id` and/or an `error` object, so check each entry.
  (The older 2026-03-01 reference names the per-call field `metadata`; we follow the current version.)

Numbers are never logged or printed unmasked. Secrets come from env only.

CLI (dry run prints the exact body, number masked in the printout only):
  python -m ember.notify.cartesia --dry-run --fixture-alert 20 --to +15555550100
"""

import argparse
import asyncio
import copy
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

import httpx

from ember.contracts import Alert
from ember.risk.alerts import P_WORDS, REPO, _spoken_list, p_words

log = logging.getLogger(__name__)

URL = "https://api.cartesia.ai/agents/calls"
ENV_VARS = ("CARTESIA_API_KEY", "CARTESIA_AGENT_ID", "CARTESIA_FROM_NUMBER_ID", "CARTESIA_VERSION")
E164 = re.compile(r"^\+[1-9]\d{6,14}$")
_P_TOKEN = re.compile(r"\bP (\d{1,3})%")  # template text: "(P 31%)"
_ANY_PCT = re.compile(r"\b(\d{1,3})\s?%")
_VOICE_P = re.compile(r"Fire is (.+?) to reach")  # risk.alerts._render voice_script
_TEXT_P = re.compile(r"fire (.+?) within")  # risk.alerts._render text
_WORDS = {w for _, w in P_WORDS} | {"unlikely"}
TIMEOUT_S = 15.0


@dataclass
class CallResult:
    ok: bool
    call_id: str | None
    call_status: str  # "placed" | "unconfigured" | "error" | "dry_run" | "offline"
    error: str | None = None


def configured() -> bool:
    return all(os.environ.get(k, "").strip() for k in ENV_VARS)


def mask(number: str) -> str:
    return f"•••• {number[-4:]}"


def _spoken_time(ms: int | None, tz: ZoneInfo) -> str:
    if ms is None:
        return "an unknown time"  # same words as risk.alerts template
    moment = datetime.fromtimestamp(ms / 1000, tz)
    return f"{int(moment.strftime('%I'))}:{moment.strftime('%M %p')}"


@lru_cache(maxsize=8)
def _zone_names(scenario_id: str) -> dict[str, str]:
    """Zone names from the scenario's web statics (00 §4 paths); {} if none are on disk."""
    for path in (REPO / "data" / "scenarios" / scenario_id / "web" / "zones.geojson",
                 REPO / "fixtures" / "static" / scenario_id / "zones.geojson"):
        if path.exists():
            features = json.loads(path.read_text(encoding="utf-8"))["features"]
            return {f["properties"]["zone_id"]: f["properties"]["name"] for f in features}
    return {}


def _zones_spoken(alert: Alert) -> str:
    """The zone list exactly as the voice_script reads it (risk.alerts.template shortens only long merges)."""
    names = [_zone_names(alert.scenario_id).get(z, z) for z in alert.zone_ids]
    more = len(names) - 3
    candidates = [_spoken_list(names)]
    if more > 0:
        candidates += [_spoken_list(names[:3] + [f"{more} more zones"]),  # template wording
                       _spoken_list(names[:3] + [f"{more} more zone{'s' if more > 1 else ''}"])]
    return next((c for c in candidates if c in alert.voice_script), candidates[-1])


def _p_words(alert: Alert) -> str:
    """The probability word the template already wrote, so the agent never contradicts the script."""
    for pattern, source in ((_VOICE_P, alert.voice_script), (_TEXT_P, alert.text)):
        m = pattern.search(source)
        if m and m.group(1) in _WORDS:
            return m.group(1)
    # Fallback for Claude-drafted text: the whole-percent figure, which can pick a neighbouring word near a threshold.
    m = _P_TOKEN.search(alert.text) or _ANY_PCT.search(alert.text)
    return p_words(int(m.group(1)) / 100) if m else "not stated"


def dynamic_variables(alert: Alert, tz_name: str = "America/Los_Angeles") -> dict[str, str]:
    tz = ZoneInfo(tz_name)
    return {
        "alert_id": alert.alert_id,
        "voice_script": alert.voice_script,
        "zones_spoken": _zones_spoken(alert),
        "act_by_spoken": _spoken_time(alert.act_by_sim_ms, tz),
        "p_words": _p_words(alert),
        "issued_spoken": _spoken_time(alert.triggered_sim_ms, tz),
    }


def build_request(alert: Alert, to_number: str) -> dict:
    return {
        "agent_id": os.environ.get("CARTESIA_AGENT_ID", ""),
        "from_number_id": os.environ.get("CARTESIA_FROM_NUMBER_ID", ""),
        "outbound_calls": [{"to_number": to_number, "dynamic_variables": dynamic_variables(alert)}],
    }


def masked_request(body: dict) -> dict:
    out = copy.deepcopy(body)
    for call in out["outbound_calls"]:
        call["to_number"] = mask(call["to_number"])
    return out


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ['CARTESIA_API_KEY']}",
        "Cartesia-Version": os.environ["CARTESIA_VERSION"],
    }


def _scrub(text: str, to_number: str) -> str:
    return text.replace(to_number, mask(to_number))[:300]


def _parse(resp: httpx.Response, to_number: str) -> CallResult:
    if resp.status_code != 200:
        return CallResult(False, None, "error", _scrub(f"HTTP {resp.status_code}: {resp.text}", to_number))
    calls = resp.json().get("calls") or []
    entry = next((c for c in calls if c.get("number") == to_number), calls[0] if calls else None)
    if entry is None:
        return CallResult(False, None, "error", "200 with no calls[] entry")
    if entry.get("error"):
        return CallResult(False, entry.get("agent_call_id"), "error",
                          _scrub(json.dumps(entry["error"], ensure_ascii=False), to_number))
    if not entry.get("agent_call_id"):
        return CallResult(False, None, "error", "200 entry without agent_call_id")
    return CallResult(True, entry["agent_call_id"], "placed")


async def place_call(alert: Alert, to_number: str, *, dry_run: bool = False,
                     client: httpx.AsyncClient | None = None) -> CallResult:
    """Never raises. dry_run builds the body and stops (ok=False: nothing was placed)."""
    try:
        if not E164.match(to_number or ""):
            return CallResult(False, None, "error", "to_number is not E.164")
        body = build_request(alert, to_number)
        if dry_run:
            log.info("cartesia dry run for %s to %s", alert.alert_id, mask(to_number))
            return CallResult(False, None, "dry_run")
        if not configured():
            return CallResult(False, None, "unconfigured", f"missing one of {', '.join(ENV_VARS)}")
        if client is None:
            async with httpx.AsyncClient(timeout=TIMEOUT_S) as own:
                resp = await own.post(URL, json=body, headers=_headers())
        else:
            resp = await client.post(URL, json=body, headers=_headers())
        result = _parse(resp, to_number)
    except httpx.HTTPError as exc:
        result = CallResult(False, None, "error", f"network: {type(exc).__name__}")
    except Exception as exc:  # contract: never raises
        result = CallResult(False, None, "error", f"{type(exc).__name__}")
    log.info("cartesia call %s to %s: %s %s", alert.alert_id, mask(to_number), result.call_status, result.error or "")
    return result


def main() -> None:
    from ember.ai.briefing import fixture_bundle, load_env  # lazy: CLI only

    ap = argparse.ArgumentParser(description="Cartesia outbound call for one fixture alert.")
    ap.add_argument("--fixture-alert", type=int, required=True, help="n-th post-ignition fixture cycle")
    ap.add_argument("--to", required=True, help="E.164 number")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    load_env()
    alert = next(a for a in fixture_bundle(args.fixture_alert).alerts if a.state == "proposed")
    print(f"POST {URL}")
    print(json.dumps(masked_request(build_request(alert, args.to)), indent=2, ensure_ascii=False))
    result = asyncio.run(place_call(alert, args.to, dry_run=args.dry_run))
    print(result)


if __name__ == "__main__":
    main()
