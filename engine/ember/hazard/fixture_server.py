"""Loopback-only, read-only fixture host with explicit development CORS origins.

This serves generated assets for Phase A; it does not implement dashboard FileSource.
"""

from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit


class FixtureHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, directory, allowed_origins=(), **kwargs):
        self.root = Path(directory).resolve()
        self.allowed_origins = frozenset(allowed_origins)
        super().__init__(*args, directory=str(self.root), **kwargs)

    def _permitted(self):
        origin = self.headers.get("Origin")
        if origin and origin not in self.allowed_origins:
            self.send_error(403, "Origin is not allowed")
            return False
        parts = Path(unquote(urlsplit(self.path).path)).parts
        if ".." in parts or any(
            p.startswith(".") for p in parts if p not in ("/", ".")
        ):
            self.send_error(403, "Path is not allowed")
            return False
        resolved = Path(super().translate_path(self.path)).resolve()
        if not resolved.is_relative_to(self.root):
            self.send_error(403, "Path escapes fixture root")
            return False
        return True

    def end_headers(self):
        origin = self.headers.get("Origin")
        if origin in self.allowed_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_GET(self):
        if self._permitted():
            super().do_GET()

    def do_HEAD(self):
        if self._permitted():
            super().do_HEAD()

    def do_OPTIONS(self):
        if self._permitted():
            self.send_response(204)
            self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
            self.end_headers()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--allow-origin", action="append", default=[])
    args = parser.parse_args()
    root = args.root.resolve()
    verification = json.loads((root / "verification.json").read_text())
    if (
        not verification.get("synthetic")
        or verification.get("scenario_id") != "rust-fixture"
    ):
        parser.error(
            "root must be an explicitly synthetic Rust fixture output directory"
        )
    for origin in args.allow_origin:
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            parser.error("allow-origin must be an exact scheme://host:port origin")
    handler = partial(FixtureHandler, directory=root, allowed_origins=args.allow_origin)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"Synthetic fixture: http://127.0.0.1:{server.server_port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
