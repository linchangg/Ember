"""Seed a disposable local Phase E hub with generated fixture bundles, never production data.

PYTHONPATH=engine python tools/seed_dashboard_hub.py ROOT SCENARIO CREDENTIALS --uri http://127.0.0.1:3017 --database phase-e-demo
The module must be freshly published or already use this private credentials file.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlparse
from ember.contracts import ActivityEvent, CycleBundle
from ember.run import load_payload
from ember.sync.stdb_http import StdbHttp

async def seed(a):
    if urlparse(a.uri).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("Test seeding is restricted to a local disposable hub")
    root, credentials = Path(a.root), Path(a.credentials)
    hub = StdbHttp(a.uri, a.database, None)
    try:
        if not credentials.exists():
            engine, token = await hub.new_identity()
            service, service_token = await hub.new_identity()
            pin = os.urandom(6).hex()
            await hub.call("set_identities", engine, service, pin)
            # Create with restrictive permissions, including before the first bytes are written.
            fd = os.open(credentials, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(dict(STDB_TOKEN=token, STDB_SERVICE_TOKEN=service_token, OPERATOR_PIN=pin), f)
        private = json.loads(credentials.read_text())
        hub.token = private["STDB_TOKEN"]
        payload = load_payload(root / "static" / a.scenario)
        await hub.load_scenario(payload)
        if a.reset:
            await hub.call("reset_scenario", a.scenario, private["OPERATOR_PIN"])
            await hub.load_scenario(payload)
        await hub.call("set_pause_on_proposal", a.scenario, False, private["OPERATOR_PIN"])
        index = json.loads((root / "cache" / a.scenario / "main/index.json").read_text())
        for i in range(0, len(index["cycles"]), 10):
            await hub.publish_bundles([CycleBundle.model_validate_json((root / e["bundle"]).read_text()) for e in index["cycles"][i:i+10]])
        issue = index["cycles"][-1]["knowledge_sim_ms"]
        await hub.call("clock_seek", a.scenario, issue, private["OPERATOR_PIN"])
        event = ActivityEvent(event_id="ev_phase_e_idempotence",scenario_id=a.scenario,branch_id="main",sim_ms=issue,wall_ms=0,stage="system",actor="engine",level="info",message="Synthetic Phase E publication acceptance")
        await hub.append_events([event])
        await hub.append_events([event])
        print("Seeded disposable local hub; credentials saved privately; notification worker is off.")
    finally:
        await hub.aclose()

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root");p.add_argument("scenario");p.add_argument("credentials")
    p.add_argument("--uri", default="http://127.0.0.1:3017")
    p.add_argument("--database", default="phase-e-demo")
    p.add_argument("--reset", action="store_true", help="reset only the named scenario in this disposable local hub")
    asyncio.run(seed(p.parse_args()))
