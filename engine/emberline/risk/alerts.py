"""Frozen alert rules -> alert proposals with template text. spec/03_engine.md §8, spec/00_contracts.md §7.

Pure: the only I/O is load_rules() reading config/alert_rules.yaml.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from emberline.contracts import Alert, ZoneRisk
from emberline.risk.risk import p_at

REPO = Path(__file__).resolve().parents[3]
PST = timezone(timedelta(hours=-8))  # January display time, spec/00 §1
ACTIVE = {"proposed", "approved", "calling", "called", "acknowledged"}
P_WORDS = ((0.9, "very likely"), (0.7, "likely"), (0.5, "more likely than not"), (0.3, "possible"))
LEVEL_TOKEN = {"warning": ("WARNING", "a warning"), "order_recommended": ("EVACUATION ORDER", "an evacuation order")}
MAX_TEXT_CHARS = 300
MAX_VOICE_WORDS = 110


def p_words(p: float) -> str:
    for threshold, words in P_WORDS:
        if p >= threshold:
            return words
    return "unlikely"


def load_rules(path: Path = REPO / "config" / "alert_rules.yaml") -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@dataclass
class AlertResult:
    new: list[Alert]
    superseded: list[Alert]

    @property
    def all(self) -> list[Alert]:
        return self.new + self.superseded


def _hm(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, PST).strftime("%H:%M")


def _spoken_time(ms: int) -> str:
    moment = datetime.fromtimestamp(ms / 1000, PST)
    return f"{int(moment.strftime('%I'))}:{moment.strftime('%M %p')}"


def _spoken_list(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def _article(word: str) -> str:
    return f"an {word}" if word[:1].lower() in "aeiou" else f"a {word}"


def _render(level: str, zones_text: str, zones_spoken: str, types: list[str], pop: int, p: float, lead_h: int,
            act_by_ms: int | None, k_ms: int, data_through_ms: int) -> tuple[str, str]:
    token, level_words = LEVEL_TOKEN[level]
    act_hm = _hm(act_by_ms) if act_by_ms is not None else "unknown"
    act_spoken = _spoken_time(act_by_ms) if act_by_ms is not None else "an unknown time"
    critical = "".join(f", {t}" for t in types)
    critical_spoken = f", including {_spoken_list([_article(t) for t in types])}" if types else ""
    lead_words = "1 hour" if lead_h == 1 else f"{lead_h} hours"
    text = (
        f"DEMO REPLAY (Jan 2025). {token} recommended — {zones_text}: "
        f"fire {p_words(p)} within {lead_h} h (P {p:.0%}). Act by {act_hm} PST. "
        f"~{pop:,} residents{critical}. Forecast {_hm(k_ms)}, satellite data through {_hm(data_through_ms)}."
    )
    voice = (
        "This is an Emberline demo replay of January 2025. Briefing for the duty officer. "
        f"Recommend {level_words} for {zones_spoken}. Fire is {p_words(p)} to reach the zone within {lead_words}. "
        f"Act by {act_spoken}. About {pop} residents{critical_spoken}. "
        "This is decision support; you make the call. Say 'acknowledged' to confirm."
    )
    return text, voice


def template(level, zones: list[ZoneRisk], zone_names, critical_types, p: float, lead_h: int,
             act_by_ms: int | None, k_ms: int, data_through_ms: int) -> tuple[str, str]:
    """(text, voice_script). Facility TYPES only, never names. Long merges list 3 zones + "+N more"."""
    ids = [z.zone_id for z in zones]
    names = [zone_names.get(i, i) for i in ids]
    types = list(dict.fromkeys(t for i in ids for t in critical_types.get(i, [])))
    pop = sum(z.population for z in zones)
    text, voice = _render(level, ", ".join(ids), _spoken_list(names), types, pop, p, lead_h,
                          act_by_ms, k_ms, data_through_ms)
    more = len(ids) - 3
    if more <= 0 or (len(text) <= MAX_TEXT_CHARS and len(voice.split()) <= MAX_VOICE_WORDS):
        return text, voice
    return _render(level, f"{', '.join(ids[:3])} +{more} more", _spoken_list(names[:3] + [f"{more} more zones"]),
                   types, pop, p, lead_h, act_by_ms, k_ms, data_through_ms)


def _holds(rule: dict, risk: ZoneRisk, k_ms: int) -> bool:
    if p_at(risk.p_by_lead, rule["lead_min"]) >= rule["p_min"]:
        return True
    within = rule.get("act_by_within_min")
    return within is not None and risk.act_by_sim_ms is not None and risk.act_by_sim_ms - k_ms <= within * 60_000


def _blocked(zone_id: str, level: str, known: list[Alert], k_ms: int, cooldown_ms: int) -> bool:
    for prev in known:
        if zone_id not in prev.zone_ids:
            continue
        if prev.level == level and (prev.state in ACTIVE or k_ms - prev.triggered_sim_ms < cooldown_ms):
            return True
        if level == "warning" and prev.level == "order_recommended" and prev.state in ACTIVE:
            return True
    return False


def evaluate(
    risks: list[ZoneRisk],
    hub_alerts: list[Alert],
    k_ms: int,
    cycle_id: str,
    scenario_id: str,
    branch_id: str,
    rules: dict,
    zone_names: dict[str, str],
    critical_types: dict[str, list[str]],
    data_through_ms: int,
) -> AlertResult:
    assert data_through_ms <= k_ms, f"data_through {data_through_ms} is after the issue time {k_ms}"
    cooldown_ms = rules["cooldown_min"] * 60_000
    ranked = sorted(risks, key=lambda r: r.rank)
    eligible = [r for r in ranked if r.population >= rules["min_population"]]
    # Orders first, so condition (c) blocks a same-cycle warning for the same zone.
    ordered_rules = sorted(rules["rules"], key=lambda rule: rule["level"] != "order_recommended")
    new: list[Alert] = []
    for rule in ordered_rules:
        level = rule["level"]
        fired = [
            r for r in eligible
            if _holds(rule, r, k_ms) and not _blocked(r.zone_id, level, hub_alerts + new, k_ms, cooldown_ms)
        ]
        groups = [fired] if len(fired) >= rules["merge_min_zones"] else [[r] for r in fired]
        lead_h = rule["lead_min"] // 60
        for group in groups:
            top = group[0]
            p = p_at(top.p_by_lead, rule["lead_min"])
            text, voice = template(level, group, zone_names, critical_types, p, lead_h,
                                   top.act_by_sim_ms, k_ms, data_through_ms)
            new.append(
                Alert(
                    alert_id=f"al_{branch_id}_{top.zone_id}_{rule['id']}_{k_ms}",
                    scenario_id=scenario_id,
                    branch_id=branch_id,
                    zone_ids=[r.zone_id for r in group],
                    level=level,
                    rule_id=rule["id"],
                    rule_text=rule["text"],
                    cycle_id=cycle_id,
                    triggered_sim_ms=k_ms,
                    act_by_sim_ms=top.act_by_sim_ms,
                    state="proposed",
                    text=text,
                    voice_script=voice,
                    draft_source="template",
                )
            )
    superseded: list[Alert] = []
    if rules.get("supersede", True):
        order_zones = {z for a in new if a.level == "order_recommended" for z in a.zone_ids}
        for prev in hub_alerts:
            if prev.state == "proposed" and prev.level == "warning" and order_zones.intersection(prev.zone_ids):
                superseded.append(prev.model_copy(update={"state": "superseded"}))
    return AlertResult(new=new, superseded=superseded)
