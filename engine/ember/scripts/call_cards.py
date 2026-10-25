"""Precomputed duty-officer call cards for file-mode replay. spec/05 §4, WAVE2 §1.1.

No engine runs during the file-mode demo, so the Cartesia call cannot fire live; the dashboard shows the call
from <assets>/cache/{scn}/{branch}/calls.json instead. Every card is built from the alert the bundle carries,
through the same notify.cartesia request builder the engine uses, so no number appears that the alert lacks.
No phone number, key or env value is ever written: env-derived request fields hold the env var name.

    uv run python -m ember.scripts.call_cards --assets <dir> --scenario eaton --branches main,satellites-only,openloop
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from ember.contracts import Alert, Briefing
from ember.notify import cartesia
from ember.risk.alerts import PST

RECIPIENT = "Duty officer"
PROMPT_VERSION = "cartesia_agent_v1"  # ai/prompts/cartesia_agent_v1.md
# The request fields build_request fills from env; the card names the env var, never its value.
ENV_FIELDS = {"agent_id": "CARTESIA_AGENT_ID", "from_number_id": "CARTESIA_FROM_NUMBER_ID"}
TO_ENV = "DUTY_OFFICER_NUMBERS"


def would_call(alert: Alert) -> bool:
    """The engine's exact trigger, sync/poller.py Poller._on_alert_approved: on an alert_approved command it
    calls only when `alert.state == "approved" and alert.decided_by` (no level filter; spec/05 §4 "On
    alert_approved"). Approval is the operator's, never the engine's (spec/05 §6), so precomputed bundles hold
    proposals; a card exists for every alert an operator could approve or already approved."""
    return alert.state == "approved" and bool(alert.decided_by)


def callable_alert(alert: Alert) -> bool:
    # web fileSource.decide() approves only state "proposed" and sets decided_by, which makes would_call true.
    return alert.state == "proposed" or would_call(alert)


def request(alert: Alert) -> dict:
    """build_request (the body place_call(dry_run=True) builds and stops at), env fields replaced by their names."""
    body = cartesia.build_request(alert, "")
    body.update({k: f"${{{v}}}" for k, v in ENV_FIELDS.items()})
    body["outbound_calls"][0]["to_number"] = f"${{{TO_ENV}}}"
    return {"method": "POST", "url": cartesia.URL, "body": body}


def transcript(dv: dict[str, str]) -> list[dict[str, str]]:
    """The call as cartesia_agent_v1.md scripts it, from the dynamic variables only."""
    return [
        {"speaker": "Ember", "text": dv["voice_script"]},  # "Read {{voice_script}} first, verbatim."
        {"speaker": RECIPIENT, "text": "Acknowledged."},  # the voice_script asks for "acknowledged"
        {"speaker": "tool", "text": f"acknowledge({dv['alert_id']})"},  # "call acknowledge with {{alert_id}}"
        {"speaker": "Ember",  # "confirm, and end the call politely"
         "text": f"Acknowledged for {dv['zones_spoken']}, act by {dv['act_by_spoken']}. You make the call. Goodbye."},
    ]


def card(alert: Alert, briefing: Briefing | None) -> dict:
    req = request(alert)
    dv = req["body"]["outbound_calls"][0]["dynamic_variables"]
    names = cartesia._zone_names(alert.scenario_id)
    return {
        "call_id": f"call_{alert.alert_id}",
        "alert_id": alert.alert_id,
        "branch_id": alert.branch_id,
        "trigger": "operator_approval",
        "trigger_sim_ms": alert.triggered_sim_ms,  # earliest moment an operator can approve it
        "level": alert.level,
        "zone_ids": alert.zone_ids,
        "zone_names": [names.get(z, z) for z in alert.zone_ids],
        "act_by_sim_ms": alert.act_by_sim_ms,
        "recipient": RECIPIENT,
        "prompt_version": PROMPT_VERSION,
        "dynamic_variables": dv,
        "request": req,
        "transcript": transcript(dv),
        "briefing": None if briefing is None else {
            "briefing_id": briefing.briefing_id, "headline": briefing.headline,
            "bullets": briefing.bullets, "text": briefing.text},
    }


def branch_cards(assets: Path, scenario: str, branch: str) -> list[dict] | None:
    index_path = assets / "cache" / scenario / branch / "index.json"
    if not index_path.exists():
        return None
    index = json.loads(index_path.read_text(encoding="utf-8"))
    alerts: dict[str, Alert] = {}  # first callable appearance per alert_id, in bundle order
    briefings: dict[str, Briefing] = {}
    for entry in index["cycles"]:
        bundle = json.loads((assets / entry["bundle"]).read_text(encoding="utf-8"))
        for raw in bundle.get("alerts", []):
            a = Alert.model_validate(raw)
            if a.alert_id not in alerts and callable_alert(a):
                alerts[a.alert_id] = a
        for raw in bundle.get("briefings", []):
            b = Briefing.model_validate(raw)
            if b.grounded and not b.fallback_used:  # validated only (ai/briefing.py)
                briefings[b.alert_id] = b
    ordered = sorted(alerts.values(), key=lambda a: a.triggered_sim_ms)  # stable: ties keep bundle rank
    return [card(a, briefings.get(a.alert_id)) for a in ordered]


def _hm(ms: int | None) -> str:
    return "--:--" if ms is None else datetime.fromtimestamp(ms / 1000, PST).strftime("%m-%d %H:%M")


def main() -> None:
    ap = argparse.ArgumentParser(description="Write calls.json (precomputed duty-officer call cards) per branch.")
    ap.add_argument("--assets", type=Path, required=True)
    ap.add_argument("--scenario", default="eaton")
    ap.add_argument("--branches", default="main")
    args = ap.parse_args()
    for branch in [b.strip() for b in args.branches.split(",") if b.strip()]:
        cards = branch_cards(args.assets, args.scenario, branch)
        if cards is None:
            print(f"[{branch}] no index.json, skipped")
            continue
        out = args.assets / "cache" / args.scenario / branch / "calls.json"
        out.write_text(json.dumps(cards, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"[{branch}] {len(cards)} call(s) -> {out}")
        for c in cards:
            print(f"  {_hm(c['trigger_sim_ms'])} PST  {c['level']:<17} act by {_hm(c['act_by_sim_ms'])}  "
                  f"{', '.join(c['zone_names'])}  briefing={'yes' if c['briefing'] else 'no'}")


if __name__ == "__main__":
    main()
