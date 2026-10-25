"""Read-only localhost scenario asset host; partial replay requires an explicit flag."""

from __future__ import annotations
import argparse
from functools import partial
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit
from ember.hazard.fixture_server import FixtureHandler


def inventory(root: Path, scenario: str):
    if not scenario or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        for c in scenario
    ):
        raise ValueError("Invalid scenario ID")
    manifest = json.loads((root / "static" / scenario / "manifest.json").read_text())
    index = json.loads((root / "cache" / scenario / "main/index.json").read_text())
    if (
        manifest["scenario_id"] != scenario
        or index["scenario_id"] != scenario
        or index["branch_id"] != "main"
    ):
        raise ValueError("Scenario asset identity mismatch")
    expected = set(
        range(
            manifest["replay_start_sim_ms"], manifest["replay_end_sim_ms"] + 1, 300000
        )
    )
    issues = [x["knowledge_sim_ms"] for x in index["cycles"]]
    if len(set(issues)) != len(issues) or not set(issues).issubset(expected):
        raise ValueError("Invalid issue inventory")
    return dict(
        scenario_id=scenario,
        indexed=len(issues),
        expected=len(expected),
        complete=set(issues) == expected,
        synthetic=bool(manifest.get("is_fixture")),
    )


class AssetHandler(FixtureHandler):
    def _permitted(self):
        parts = Path(unquote(urlsplit(self.path).path)).parts
        if (
            len(parts) < 3
            or parts[1] not in ("static", "cache")
            or parts[2] != self.scenario
        ):
            self.send_error(403, "Only selected scenario assets are served")
            return False
        if (
            "checkpoints" in parts
            or parts[-1] in ("dispatch.sqlite3", "branch-evidence.json")
            or parts[-1].endswith((".npz", ".key", ".sqlite3"))
        ):
            self.send_error(403, "Internal engine state is not a public asset")
            return False
        return super()._permitted()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--scenario", required=True)
    p.add_argument("--port", type=int, default=8798)
    p.add_argument("--allow-origin", action="append", default=[])
    p.add_argument("--allow-partial", action="store_true")
    a = p.parse_args()
    status = inventory(a.root, a.scenario)
    if not status["complete"] and not a.allow_partial:
        p.error(
            "Replay is partial; use --allow-partial for an explicitly labelled rehearsal"
        )
    for origin in a.allow_origin:
        u = urlsplit(origin)
        if (
            u.scheme not in ("http", "https")
            or not u.netloc
            or u.path
            or u.query
            or u.fragment
        ):
            p.error("Expected exact scheme://host:port origin")
    AssetHandler.scenario = a.scenario
    handler = partial(AssetHandler, directory=a.root, allowed_origins=a.allow_origin)
    server = ThreadingHTTPServer(("127.0.0.1", a.port), handler)
    print(json.dumps({**status, "url": f"http://127.0.0.1:{a.port}/"}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
