"""Claude briefing writer. spec/05_ai_voice.md §1.

Claude only rephrases the alert. Every number it writes is checked by
ai/validate.py; on any failure the alert keeps its template text.

CLI (one real call, needs ANTHROPIC_API_KEY):
    uv run python -m emberline.ai.briefing --fixture-cycle 20
"""

import argparse
import asyncio
import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic

from emberline.ai.validate import validate
from emberline.contracts import Alert, Briefing, Cycle, CycleBundle, SensorInfo, ZoneRisk, lead_idx

MODEL = "claude-sonnet-5-5"
PROMPT_VERSION = "briefing_v1"
MAX_INPUT_CHARS = 4096
HONESTY = "Retrospective replay of Jan 7–8 2025. Decision support only."
CRITICAL_TYPES = ("hospital", "nursing_home", "assisted_living", "school")  # spec/01 §6 n_critical

ENGINE = Path(__file__).resolve().parents[2]
REPO = ENGINE.parent
SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / f"{PROMPT_VERSION}.md").read_text(encoding="utf-8")
SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "bullets": {"type": "array", "items": {"type": "string"}},
        "text": {"type": "string"},
        "voice_script": {"type": "string"},
    },
    "required": ["headline", "bullets", "text", "voice_script"],
    "additionalProperties": False,
}

# January is PST (UTC−8). A fixed offset avoids tzdata on Windows.
PST = timezone(timedelta(hours=-8))


def hm(ms: int | None) -> str | None:
    return None if ms is None else datetime.fromtimestamp(ms / 1000, PST).strftime("%H:%M")


def _zone_entry(
    zid: str, r: ZoneRisk, name: str, egress: int | None, critical: list[str]
) -> dict:
    return {
        "zone_id": zid,
        "name": name,
        "p_60": round(r.p_by_lead[lead_idx(60)], 2),
        "p_120": round(r.p_by_lead[lead_idx(120)], 2),
        "eta_q50_pst": hm(r.eta_q50_sim_ms),
        "act_by_pst": hm(r.act_by_sim_ms),
        "evac_min": round(r.evac_min),
        "population": r.population,
        "critical": critical,
        "egress_roads": egress,
        "trend": r.trend,
    }


def _sensor_entry(s: SensorInfo) -> dict:
    key = "last_pass_pst" if s.kind == "viirs_pixel" else "last_scan_pst"
    return {"name": s.name, key: hm(s.last_data_sim_ms), "status": s.status}


def build_input(
    alert: Alert,
    risks: list[ZoneRisk],
    zone_names: dict[str, str],
    egress_roads: dict[str, int],
    critical_types: dict[str, list[str]],
    cycle: Cycle,
    sensors: list[SensorInfo],
    k_ms: int,
) -> dict:
    assert cycle.data_through_sim_ms <= k_ms, f"future data: data_through {cycle.data_through_sim_ms} > k {k_ms}"
    by_zone = {r.zone_id: r for r in risks}
    zones = [
        _zone_entry(z, by_zone[z], zone_names.get(z, z), egress_roads.get(z), critical_types.get(z, []))
        for z in alert.zone_ids
    ]
    inp = {
        "issued_pst": hm(k_ms),
        "data_through_pst": hm(cycle.data_through_sim_ms),
        "forecast_age_min": round((k_ms - cycle.knowledge_sim_ms) / 60000),
        "alert": {"level": alert.level, "rule_text": alert.rule_text},
        "zones": zones,
        "fire": {
            "burned_ha": round(cycle.burned_ha),
            "growth_ha_per_h": round(cycle.growth_ha_per_h),
            "head_bearing_deg": None if cycle.head_bearing_deg is None else round(cycle.head_bearing_deg),
        },
        "sensors": [_sensor_entry(s) for s in sensors],
        "honesty": HONESTY,
    }
    if len(json.dumps(inp)) > MAX_INPUT_CHARS:
        inp["zones"] = zones[:3]
    assert len(json.dumps(inp)) <= MAX_INPUT_CHARS, f"briefing input {len(json.dumps(inp))} chars > {MAX_INPUT_CHARS}"
    return inp


def input_hash(inp: dict) -> str:
    blob = json.dumps(inp, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def load_env(path: Path = ENGINE / ".env") -> None:
    """Tiny KEY=VALUE loader; never overrides variables already set."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class _Retryable(Exception):
    pass


async def _call(client, inp: dict) -> dict:
    resp = await client.messages.create(
        model=MODEL,
        max_tokens=2000,
        thinking={"type": "between_tools"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(inp, ensure_ascii=False)}],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
    )
    if resp.stop_reason == "refusal":
        raise _Retryable("refusal")
    text = next((b.text for b in resp.content if b.type == "text"), None)
    if text is None:
        raise _Retryable("no text block")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise _Retryable("bad json") from e
    if not isinstance(data, dict) or set(SCHEMA["required"]) - set(data):
        raise _Retryable("bad shape")
    return data


async def _call_with_retry(client, inp: dict) -> dict:
    try:
        return await _call(client, inp)
    except _Retryable:
        return await _call(client, inp)


async def draft(inp: dict, alert: Alert, cycle: Cycle, client=None) -> Briefing:
    """Never raises. Claude's text if it validates, else the alert's template text."""
    t0 = time.perf_counter()
    data: dict | None = None
    unmatched: list[str] = []
    try:
        if client is None:
            load_env()
            client = anthropic.AsyncAnthropic(timeout=20.0)
        out = await _call_with_retry(client, inp)
        ok, unmatched = validate(out, inp, alert.level)
        data = out if ok else None
    except Exception as e:  # noqa: BLE001: the engine loop must never crash because of Claude
        detail = f" {e}" if isinstance(e, _Retryable) else ""
        unmatched = [f"error: {type(e).__name__}{detail}"]
    latency_ms = int((time.perf_counter() - t0) * 1000)
    if data is None:
        data = {
            "headline": alert.text[:90],
            "bullets": [],
            "text": alert.text,
            "voice_script": alert.voice_script,
        }
    return Briefing(
        briefing_id=f"brf_{alert.alert_id}",
        scenario_id=alert.scenario_id,
        branch_id=alert.branch_id,
        cycle_id=cycle.cycle_id,
        alert_id=alert.alert_id,
        model=MODEL,
        prompt_version=PROMPT_VERSION,
        input_hash=input_hash(inp),
        headline=data["headline"],
        bullets=list(data["bullets"]),
        text=data["text"],
        voice_script=data["voice_script"],
        grounded=not unmatched,
        unmatched_tokens=unmatched,
        fallback_used=bool(unmatched),
        latency_ms=latency_ms,
        created_wall_ms=int(time.time() * 1000),
    )


# --- fixture helpers (CLI and tests) -------------------------------------------------


def _features(name: str) -> list[dict]:
    path = REPO / "fixtures" / "static" / "fixture" / name
    return [f["properties"] for f in json.loads(path.read_text(encoding="utf-8"))["features"]]


def fixture_statics() -> tuple[dict[str, str], dict[str, int], dict[str, list[str]]]:
    """zone_names, egress_roads, critical_types (types only, never names) from fixtures/static/fixture."""
    zones = _features("zones.geojson")
    critical: dict[str, list[str]] = {}
    for a in _features("assets.geojson"):
        if a["type"] in CRITICAL_TYPES and a.get("zone_id"):
            critical.setdefault(a["zone_id"], []).append(a["type"].replace("_", " "))
    return (
        {z["zone_id"]: z["name"] for z in zones},
        {z["zone_id"]: z["egress_roads"] for z in zones},
        critical,
    )


def fixture_bundle(n: int) -> CycleBundle:
    """The n-th post-ignition cycle (1-based; pre-ignition bundles have no cycle)."""
    index = json.loads((REPO / "fixtures" / "cache" / "fixture" / "main" / "index.json").read_text(encoding="utf-8"))
    bundles = (
        CycleBundle.model_validate_json((REPO / "fixtures" / c["bundle"]).read_text(encoding="utf-8"))
        for c in index["cycles"]
    )
    with_cycle = [b for b in bundles if b.cycle is not None]
    return with_cycle[n - 1]


def fixture_input(n: int) -> tuple[dict, Alert, Cycle]:
    b = fixture_bundle(n)
    alert = next(a for a in b.alerts if a.state == "proposed")
    names, egress, critical = fixture_statics()
    inp = build_input(alert, b.zone_risk, names, egress, critical, b.cycle, b.sensors, b.knowledge_sim_ms)
    return inp, alert, b.cycle


def main() -> None:
    ap = argparse.ArgumentParser(description="One Claude briefing on a fixture cycle.")
    ap.add_argument("--fixture-cycle", type=int, required=True)
    args = ap.parse_args()
    inp, alert, cycle = fixture_input(args.fixture_cycle)
    print(json.dumps(inp, indent=2, ensure_ascii=False))
    brf = asyncio.run(draft(inp, alert, cycle))
    print(brf.model_dump_json(indent=2))
    print("VALIDATED" if brf.grounded else f"FALLBACK: {brf.unmatched_tokens}")


if __name__ == "__main__":
    main()
