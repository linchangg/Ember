"""G1-b hub spike (spec/04 §1, docs/briefs/T2_hub.md phase 1). Local hub only.

uv run python -m emberline.scripts.hub_spike            # bootstrap, publish cycle 20, read back
uv run python -m emberline.scripts.hub_spike --watch 60 # also poll pending commands and print latency
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import secrets
import time
from pathlib import Path

from emberline.sync.stdb_http import ReducerRejected, StdbHttp

ENGINE = Path(__file__).resolve().parents[2]
ENV_PATH = ENGINE / ".env"
REPO = ENGINE.parent
FIXTURE_MAIN = REPO / "fixtures" / "cache" / "fixture" / "main"


def mask(secret: str) -> str:
    return f"…{secret[-4:]}"


def load_env() -> None:
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.strip().startswith("#"):
            os.environ.setdefault(key.strip(), value.split("#")[0].strip())


def append_env(values: dict[str, str]) -> None:
    with ENV_PATH.open("a", encoding="utf-8") as f:
        f.writelines(f"{k}={v}\n" for k, v in values.items())
    os.environ.update(values)


def token_identity(token: str) -> str:
    """The hex identity a SpacetimeDB token belongs to (JWT claim `hex_identity`)."""
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["hex_identity"]


async def bootstrap(uri: str, db: str) -> None:
    if not os.environ.get("STDB_TOKEN"):
        anon = StdbHttp(uri, db, None)
        engine_id, engine_tok = await anon.new_identity()
        service_id, service_tok = await anon.new_identity()
        await anon.aclose()
        append_env({"STDB_TOKEN": engine_tok, "STDB_SERVICE_TOKEN": service_tok})
        print(f"engine identity  {engine_id}  token {mask(engine_tok)}")
        print(f"service identity {service_id}  token {mask(service_tok)}")
    if not os.environ.get("OPERATOR_PIN"):
        append_env({"OPERATOR_PIN": f"{secrets.randbelow(10**6):06d}"})
        print(f"OPERATOR_PIN written to {ENV_PATH} (not printed)")


def cycle20_bundle() -> str:
    """The 20th cycle bundle. The index lists 3 pre-ignition bundles first, so this is not entry 19."""
    index = json.loads((FIXTURE_MAIN / "index.json").read_text(encoding="utf-8"))
    fired: list[Path] = []
    for entry in index["cycles"]:
        path = REPO / "fixtures" / entry["bundle"]
        if json.loads(path.read_text(encoding="utf-8")).get("cycle") is not None:
            fired.append(path)
    if len(fired) < 20:
        raise RuntimeError(f"fixture index has {len(fired)} cycles, need 20")
    return fired[19].read_text(encoding="utf-8")


async def watch(hub: StdbHttp, seconds: float) -> None:
    """Poll pending commands every 200 ms; print each new one with its hub-to-poll latency."""
    seen: set[str] = set()
    end = time.time() + seconds
    while time.time() < end:
        (rows,) = await hub.sql("SELECT * FROM command WHERE status = 'pending'")
        now_ms = time.time() * 1000
        for row in sorted(rows, key=lambda r: r["created_wall_ms"]):
            if row["command_id"] not in seen:
                seen.add(row["command_id"])
                print(f"seen {row['command_id']} kind={row['kind']} latency={now_ms - row['created_wall_ms']:.0f} ms")
        await asyncio.sleep(0.2)


async def main(watch_s: float) -> None:
    load_env()
    uri = os.environ.get("STDB_URI", "http://localhost:3000")
    db = os.environ.get("STDB_DB", "emberline")
    assert "localhost" in uri or "127.0.0.1" in uri, f"spike is local only, got STDB_URI={uri}"
    await bootstrap(uri, db)

    engine_id = token_identity(os.environ["STDB_TOKEN"])
    service_id = token_identity(os.environ["STDB_SERVICE_TOKEN"])
    hub = StdbHttp(uri, db, os.environ["STDB_TOKEN"])
    try:
        try:  # anyone may call this while config is empty; the engine identity does it over HTTP
            await hub.call("set_identities", engine_id, service_id, os.environ["OPERATOR_PIN"])
            print("set_identities: ok (over HTTP)")
        except ReducerRejected as e:
            print(f"set_identities: {e.body.strip()}")

        await hub.call("publish_bundle", cycle20_bundle())
        print("publish_bundle(cycle 20): ok")
        (cycles,) = await hub.sql("SELECT * FROM cycle")
        print("cycle rows:", json.dumps(cycles, indent=1))
        (pending,) = await hub.sql("SELECT * FROM command WHERE status = 'pending'")
        print(f"pending commands ({len(pending)}):", json.dumps(sorted(pending, key=lambda r: r["created_wall_ms"]), indent=1))
        if watch_s:
            await watch(hub, watch_s)
    finally:
        await hub.aclose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", type=float, default=0.0, help="seconds to poll pending commands")
    asyncio.run(main(ap.parse_args().watch))
