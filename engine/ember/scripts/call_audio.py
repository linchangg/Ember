"""Voices the precomputed duty-officer calls so the dashboard's call card can play them (run after call_cards).

Each spoken transcript line gets a Cartesia TTS clip, phone-band filtered, at
<assets>/cache/{scn}/call_audio/<hash>.mp3; calls.json gains `audio` on those lines and `ring_audio` per call.
Clips are keyed by voice + spoken text, so identical lines are synthesized once and re-runs only fill gaps.
The card shows the transcript text unchanged; only the spoken form turns "17:51 PST" into "5:51 PM".
Needs CARTESIA_API_KEY and CARTESIA_VERSION (engine/.env) and ffmpeg on PATH.

    uv run python -m ember.scripts.call_audio --assets .. --scenario eaton --branches main,satellites-only,openloop
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path

from ember.ai.briefing import load_env

TTS_URL = "https://api.cartesia.ai/tts/bytes"
MODEL = "sonic-3"
VOICES = {"Ember": "25d7abcb-4d6d-4aca-adce-8a1c85620c8b",  # Jessica: the call agent
          "Duty officer": "aa2cafe9-97ba-4052-ac3c-875000f95212"}  # Zander
PHONE = "highpass=f=300,lowpass=f=3400,acompressor=threshold=-18dB:ratio=3,volume=1.2"
# US ringback, 440+480 Hz, 2 s on / 0.6 s off
RING = ("sine=f=440:d=2[a];sine=f=480:d=2[b];[a][b]amix=2,volume=0.35,"
        "afade=t=in:d=0.05,afade=t=out:st=1.95:d=0.05,apad=pad_dur=0.6")
_TIME = re.compile(r"\b(\d{1,2}):(\d{2}) PST\b")


def speakable(text: str) -> str:
    """24-hour "17:51 PST" reads badly aloud; say "5:51 PM". Nothing else changes."""
    def twelve(m: re.Match) -> str:
        h = int(m.group(1))
        return f"{h % 12 or 12}:{m.group(2)} {'PM' if h >= 12 else 'AM'}"
    return _TIME.sub(twelve, text)


def clip_key(voice: str, spoken: str) -> str:
    return hashlib.sha256(f"{MODEL}|{voice}|{spoken}".encode()).hexdigest()[:16]


def tts(voice: str, text: str) -> bytes:
    body = {"model_id": MODEL, "transcript": text, "voice": {"mode": "id", "id": voice}, "language": "en",
            "output_format": {"container": "wav", "encoding": "pcm_s16le", "sample_rate": 44100}}
    req = urllib.request.Request(TTS_URL, json.dumps(body).encode(), method="POST", headers={
        "Authorization": f"Bearer {os.environ['CARTESIA_API_KEY']}",
        "Cartesia-Version": os.environ["CARTESIA_VERSION"], "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def ffmpeg(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args, "-ac", "1", "-c:a", "libmp3lame", "-b:a", "96k"], check=True)


def phone_mp3(wav: bytes, out: Path) -> None:
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "in.wav"
        src.write_bytes(wav)
        ffmpeg(["-i", str(src), "-af", PHONE, str(out)])


def annotate(cards: list[dict], scenario: str, out_dir: Path, make_clip: Callable[[str, str, Path], None]) -> int:
    """Adds `audio`/`ring_audio` (paths relative to the asset root) and makes any missing clip. Returns clips made."""
    rel = f"cache/{scenario}/call_audio"
    made = 0
    for card in cards:
        card["ring_audio"] = f"{rel}/ring.mp3"
        for line in card["transcript"]:
            voice = VOICES.get(line["speaker"])
            if voice is None:  # tool steps stay silent
                continue
            spoken = speakable(line["text"])
            name = f"{clip_key(voice, spoken)}.mp3"
            if not (out_dir / name).exists():
                make_clip(voice, spoken, out_dir / name)
                made += 1
            line["audio"] = f"{rel}/{name}"
    return made


def main() -> None:
    ap = argparse.ArgumentParser(description="Voice calls.json transcripts with Cartesia for the dashboard call card.")
    ap.add_argument("--assets", type=Path, required=True)
    ap.add_argument("--scenario", default="eaton")
    ap.add_argument("--branches", default="main")
    args = ap.parse_args()
    load_env()
    out_dir = args.assets / "cache" / args.scenario / "call_audio"
    out_dir.mkdir(parents=True, exist_ok=True)
    if not (out_dir / "ring.mp3").exists():
        ffmpeg(["-f", "lavfi", "-i", RING, str(out_dir / "ring.mp3")])
    for branch in [b.strip() for b in args.branches.split(",") if b.strip()]:
        path = args.assets / "cache" / args.scenario / branch / "calls.json"
        if not path.exists():
            print(f"[{branch}] no calls.json, skipped")
            continue
        cards = json.loads(path.read_text(encoding="utf-8"))
        made = annotate(cards, args.scenario, out_dir, lambda v, t, out: phone_mp3(tts(v, t), out))
        path.write_text(json.dumps(cards, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"[{branch}] {len(cards)} call(s), {made} new clip(s)")


if __name__ == "__main__":
    main()
