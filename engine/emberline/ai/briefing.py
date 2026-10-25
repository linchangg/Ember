"""Claude briefing writer. spec/05_ai_voice.md §1.

Claude only rephrases the alert. Every number it writes is checked by
ai/validate.py; on any failure the alert keeps its template text.

CLI (one real call; needs OPENROUTER_API_KEY or ANTHROPIC_API_KEY in env or engine/.env):
    uv run python -m emberline.ai.briefing --fixture-cycle 20

Config (env): EMBERLINE_BRIEFING_MODEL (default claude-sonnet-5-5), ANTHROPIC_BASE_URL
(e.g. https://openrouter.ai/api), OPENROUTER_API_KEY (sent as a bearer token) else ANTHROPIC_API_KEY.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import anthropic

from emberline.ai.validate import validate
from emberline.contracts import Alert, Briefing, Cycle, CycleBundle, SensorInfo, ZoneRisk, lead_idx

DEFAULT_MODEL = "claude-sonnet-5-5"
OPENROUTER_URL = "https://openrouter.ai/api"
PROMPT_VERSION = "briefing_v2"
MAX_INPUT_CHARS = 4096
HONESTY = "Retrospective replay of Jan 7–8 2025. Decision support only."
CRITICAL_TYPES = ("hospital", "nursing_home", "assisted_living", "school")  # spec/01 §6 n_critical

log = logging.getLogger(__name__)

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


def model_name() -> str:
    return os.environ.get("EMBERLINE_BRIEFING_MODEL") or DEFAULT_MODEL


def make_client() -> anthropic.AsyncAnthropic:
    """OpenRouter (bearer auth_token) if OPENROUTER_API_KEY is set, else Anthropic (api_key)."""
    # max_retries=0: spec/05 §1 allows one retry, done by _call_with_retry; SDK retries would add hidden calls.
    base_url = os.environ.get("ANTHROPIC_BASE_URL") or None
    kwargs: dict = {"timeout": 20.0, "max_retries": 0}
    if key := os.environ.get("OPENROUTER_API_KEY"):
        # The OpenRouter key only ever goes to OpenRouter. A shell ANTHROPIC_BASE_URL (Claude Code sets
        # https://api.anthropic.com) wins over engine/.env, which would send this key to Anthropic.
        if not base_url or urlparse(base_url).hostname != "openrouter.ai":
            log.warning("OPENROUTER_API_KEY set but ANTHROPIC_BASE_URL is not openrouter.ai; using %s", OPENROUTER_URL)
            base_url = OPENROUTER_URL
        return anthropic.AsyncAnthropic(auth_token=key, base_url=base_url, **kwargs)
    kwargs["base_url"] = base_url
    return anthropic.AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"), **kwargs)


class _Retryable(Exception):
    pass


async def _call(client, inp: dict, model: str) -> dict:
    log.info("briefing call model=%s", model)
    resp = await client.messages.create(
        model=model,
        max_tokens=2000,
        thinking={"type": "between_tools"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(inp, ensure_ascii=False)}],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
    )
    usage = getattr(resp, "usage", None)
    log.info(
        "briefing usage input_tokens=%s output_tokens=%s stop=%s",
        getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None), resp.stop_reason,
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


async def _call_with_retry(client, inp: dict, model: str) -> dict:
    try:
        return await _call(client, inp, model)
    except _Retryable as e:
        log.info("briefing retry after %s", e)
        return await _call(client, inp, model)


async def draft(inp: dict, alert: Alert, cycle: Cycle, client=None) -> Briefing:
    """Never raises. Claude's text if it validates, else the alert's template text."""
    t0 = time.perf_counter()
    data: dict | None = None
    unmatched: list[str] = []
    if client is None:
        load_env()
    model = model_name()
    try:
        if client is None:
            client = make_client()
        out = await _call_with_retry(client, inp, model)
        ok, unmatched = validate(out, inp, alert.level)
        if not ok:
            log.info("Claude draft rejected by validator → template used: %s; draft=%s", unmatched, json.dumps(out))
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
        model=model,
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
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    inp, alert, cycle = fixture_input(args.fixture_cycle)
    print(json.dumps(inp, indent=2, ensure_ascii=False))
    brf = asyncio.run(draft(inp, alert, cycle))
    print(brf.model_dump_json(indent=2))
    print("VALIDATED" if brf.grounded else f"FALLBACK: {brf.unmatched_tokens}")


if __name__ == "__main__":
    main()
