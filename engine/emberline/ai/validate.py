"""Grounding validator for Claude briefings. spec/05_ai_voice.md §1 "Validator".

Pure functions. Every number, time, percentage and zone ID Claude writes must
already be in the input; anything else sends the alert back to its template.
"""

import re

from emberline.risk.alerts import P_WORDS, p_words

BANNED = ("will reach", "will burn", "guaranteed", "certain", "would have saved", "evacuate now")

_PHRASES = sorted([w for _, w in P_WORDS] + ["unlikely"], key=len, reverse=True)

TEXT_PREFIX = "DEMO REPLAY (Jan 2025)."
VOICE_PREFIX = "This is an Emberline demo replay of January 2025."
VOICE_SUFFIX = "You make the call."

_TIME = re.compile(r"\b(\d{1,2}):(\d{2})(?:\s?([AaPp])\.?[Mm]\.?)?")
_PCT = re.compile(r"\b(\d{1,3}(?:\.\d+)?)\s?%")
_ZONE = re.compile(r"\b[A-Z][A-Z0-9]+-[A-Z0-9-]+\b")
_NUM = re.compile(r"\b\d[\d,]*(?:\.\d+)?\b")


def _norm_num(s: str) -> str:
    s = s.replace(",", "")
    return s[:-2] if s.endswith(".0") else s


def _norm_time(m: re.Match) -> str:
    hour, minute, ampm = int(m.group(1)), m.group(2), m.group(3)
    if ampm:
        hour = hour % 12 + (12 if ampm.lower() == "p" else 0)
    return f"{hour:02d}:{minute}"


def _blank(s: str, m: re.Match) -> str:
    return s[: m.start()] + " " * (m.end() - m.start()) + s[m.end() :]


def extract_tokens(s: str) -> list[str]:
    """Times, then percentages, then zone IDs, then numbers; each match is blanked before the next step."""
    tokens: list[str] = []
    for pattern, norm in (
        (_TIME, _norm_time),
        (_PCT, lambda m: f"{round(float(m.group(1)))}%"),
        (_ZONE, lambda m: m.group(0)),
        (_NUM, lambda m: _norm_num(m.group(0))),
    ):
        for m in list(pattern.finditer(s)):
            tokens.append(norm(m))
            s = _blank(s, m)
    return tokens


def _number_forms(x: int | float) -> set[str]:
    forms = {_norm_num(str(x))}
    if isinstance(x, int):
        forms.add(f"{x:,}")
    if 0 <= x <= 1:
        forms |= {f"{round(x * 100)}%", str(round(x * 100))}
    return forms


def allowed_tokens(inp: dict) -> set[str]:
    allowed = {"2025"}

    def walk(v: object) -> None:
        if isinstance(v, dict):
            if "zone_id" in v:
                allowed.add(str(v["zone_id"]))
                allowed.add(str(v.get("name", "")))
            for item in v.values():
                walk(item)
        elif isinstance(v, list):
            for item in v:
                walk(item)
        elif isinstance(v, str):
            allowed.update(extract_tokens(v))
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            allowed.update(_number_forms(v))

    walk(inp)
    return allowed


def _token_kind(tok: str) -> str:
    if ":" in tok:
        return "time"
    if tok.endswith("%"):
        return "pct"
    if "-" in tok:
        return "zone"
    return "num"


def _fields(out: dict) -> list[str]:
    return [out["headline"], *out["bullets"], out["text"], out["voice_script"]]


def _check_tokens(out: dict, allowed: set[str]) -> list[str]:
    failures = []
    for field in _fields(out):
        for tok in extract_tokens(field):
            if tok in allowed or (tok.isdigit() and int(tok) <= 10):
                continue
            failures.append(f"{_token_kind(tok)} {tok}")
    return failures


def _check_p_words(out: dict, inp: dict) -> list[str]:
    ok = {p_words(z[key]) for z in inp.get("zones", []) for key in ("p_60", "p_120")}
    s = " ".join(_fields(out)).lower()
    failures = []
    for phrase in _PHRASES:
        pattern = re.compile(rf"\b{re.escape(phrase)}\b")
        for m in list(pattern.finditer(s)):
            if phrase not in ok:
                failures.append(f"pword {phrase}")
            s = _blank(s, m)
    return failures


def _check_banned(out: dict, level: str) -> list[str]:
    s = " ".join(_fields(out)).lower()
    failures = []
    for phrase in BANNED:
        if phrase == "evacuate now" and level == "order_recommended":
            continue
        if re.search(rf"\b{re.escape(phrase)}\b", s):
            failures.append(f"banned: {phrase}")
    return failures


def _check_shape(out: dict) -> list[str]:
    failures = []
    if not out["text"].startswith(TEXT_PREFIX):
        failures.append("prefix text")
    if not out["voice_script"].startswith(VOICE_PREFIX):
        failures.append("prefix voice")
    if not out["voice_script"].rstrip().endswith(VOICE_SUFFIX):
        failures.append("suffix voice")
    if len(out["headline"]) > 90:
        failures.append(f"len headline {len(out['headline'])}")
    if len(out["bullets"]) > 4:
        failures.append(f"bullets {len(out['bullets'])}")
    failures += [f"len bullet {len(b)}" for b in out["bullets"] if len(b) > 120]
    if len(out["text"]) > 300:
        failures.append(f"len text {len(out['text'])}")
    words = len(out["voice_script"].split())
    if words > 110:
        failures.append(f"words voice {words}")
    return failures


def validate(out: dict, inp: dict, level: str) -> tuple[bool, list[str]]:
    """(ok, failures). `out` has headline, bullets, text, voice_script (the structured-output schema)."""
    failures = (
        _check_tokens(out, allowed_tokens(inp))
        + _check_p_words(out, inp)
        + _check_banned(out, level)
        + _check_shape(out)
    )
    return len(failures) == 0, failures
