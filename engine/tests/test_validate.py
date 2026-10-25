"""Grounding validator. spec/05_ai_voice.md §8 test 1: 12 table-driven cases plus extras."""

import copy
import json
from pathlib import Path

import pytest

from ember.ai.validate import allowed_tokens, extract_tokens, p_words, validate

# Saved from the fixture's cycle-20 alert (FIX-Z2 warning; p_60 0.02, p_120 0.30, act by 21:07).
INP = json.loads((Path(__file__).parent / "data" / "briefing_inputs" / "c20.json").read_text(encoding="utf-8"))

GOOD = {
    "headline": "Warning for Fixture Z2 (FIX-Z2): act by 21:07 PST",
    "bullets": [
        "Fire is possible to reach FIX-Z2 within 2 h (P 30%).",
        "Median arrival 22:23 PST; about 420 residents, 2 egress roads.",
        "Forecast 19:55 PST, satellite data through 19:55; data age 0 min.",
    ],
    "text": (
        "Warning for FIX-Z2: fire possible within 2 h (P 30%). "
        "Act by 21:07 PST. About 420 residents. Forecast 19:55, satellite data through 19:55."
    ),
    "voice_script": (
        "This is Ember. A warning is recommended for Fixture Z2. "
        "Fire is possible to reach the zone within 2 hours. Act by 9:07 PM. About 420 residents. "
        "Forecast issued at 7:55 PM. You make the call."
    ),
}


def mutate(field: str, old: str, new: str) -> dict:
    out = copy.deepcopy(GOOD)
    assert old in out[field], (field, old)
    out[field] = out[field].replace(old, new)
    return out


def with_bullets(bullets: list[str]) -> dict:
    out = copy.deepcopy(GOOD)
    out["bullets"] = bullets
    return out


CASES = [
    ("correct", GOOD, None),
    ("altered time", mutate("text", "21:07 PST", "21:15 PST"), "time 21:15"),
    ("altered pct", mutate("text", "P 30%", "P 35%"), "pct 35%"),
    ("altered population", mutate("text", "420 residents", "450 residents"), "num 450"),
    ("invented zone", mutate("text", "FIX-Z2:", "FIX-Z9:"), "zone FIX-Z9"),
    ("wrong p word", mutate("text", "fire possible", "fire likely"), "pword likely"),
    ("will reach", mutate("voice_script", "Fire is possible to reach", "Fire will reach"), "banned: will reach"),
    ("evacuate now at warning", with_bullets([*GOOD["bullets"], "Evacuate now."]), "banned: evacuate now"),
    ("missing voice prefix", mutate("voice_script", "This is Ember. ", ""), "prefix voice"),
    ("text over 300", mutate("text", "About 420", "Residents should " + "stay alert and " * 12 + "about 420"), "len text"),
    ("voice over 110 words", mutate("voice_script", "You make", "Stay alert. " * 45 + "You make"), "words voice"),
    ("5 bullets", with_bullets(GOOD["bullets"] + ["Stay alert.", "Watch the map."]), "bullets 5"),
]


@pytest.mark.parametrize("name,out,expected", CASES, ids=[c[0] for c in CASES])
def test_validator_table(name: str, out: dict, expected: str | None) -> None:
    ok, failures = validate(out, INP, INP["alert"]["level"])
    if expected is None:
        assert ok, failures
    else:
        assert not ok
        assert any(f.startswith(expected) for f in failures), failures


def test_evacuate_now_allowed_for_order() -> None:
    out = with_bullets([*GOOD["bullets"], "Evacuate now."])
    assert "banned: evacuate now" not in validate(out, INP, "order_recommended")[1]


def test_12h_time_matches_24h() -> None:
    assert extract_tokens("Act by 11:40 PM, then 9:05") == ["23:40", "09:05"]
    assert extract_tokens("12:10 a.m.") == ["00:10"]
    assert "23:40" in allowed_tokens({"act_by_pst": "23:40"})


def test_pct_matches_fraction() -> None:
    assert extract_tokens("P 54%") == ["54%"]
    assert "54%" in allowed_tokens({"p_60": 0.54})


def test_comma_number_matches_int() -> None:
    assert extract_tokens("2,140 residents.") == ["2140"]
    assert "2140" in allowed_tokens({"population": 2140})


def test_small_ints_pass() -> None:
    out = with_bullets(["Two roads out; 3 roads in the area."])
    assert validate(out, INP, "warning") == (True, [])


def test_very_likely_not_read_as_likely() -> None:
    inp = copy.deepcopy(INP)
    inp["zones"][0]["p_120"] = 0.92
    out = mutate("text", "fire possible", "fire very likely")
    out = {**out, "bullets": [b.replace("(P 30%)", "(P 92%)").replace("possible", "very likely") for b in out["bullets"]]}
    out["text"] = out["text"].replace("P 30%", "P 92%")
    out["voice_script"] = out["voice_script"].replace("possible", "very likely")
    assert validate(out, inp, "warning") == (True, [])
    inp["zones"][0]["p_120"] = 0.75  # now "likely", so "very likely" is wrong, and only that
    out["text"] = out["text"].replace("P 92%", "P 75%")
    out["bullets"] = [b.replace("(P 92%)", "(P 75%)") for b in out["bullets"]]
    failures = validate(out, inp, "warning")[1]
    assert failures and all(f == "pword very likely" for f in failures), failures


def test_p_words_table() -> None:
    assert [p_words(p) for p in (0.95, 0.9, 0.7, 0.5, 0.3, 0.29)] == [
        "very likely",
        "very likely",
        "likely",
        "more likely than not",
        "possible",
        "unlikely",
    ]


def test_p_words_is_the_alerts_table() -> None:
    from ember.ai import validate as v
    from ember.risk import alerts

    assert v.p_words is alerts.p_words and v.P_WORDS is alerts.P_WORDS

