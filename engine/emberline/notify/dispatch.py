"""Durable dispatch reservation. Unknown outcomes require reconciliation, never redial.

Stores no recipient, credential or voice script. The reservation is written before
calling the provider; a crash afterward is intentionally treated as uncertain.
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class DispatchRecord:
    key: str
    state: str
    call_id: str | None
    call_status: str | None


class DispatchJournal:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS dispatch (key TEXT PRIMARY KEY, state TEXT NOT NULL, call_id TEXT, call_status TEXT)"
            )
        self.path.chmod(0o600)

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def reserve(self, key: str) -> tuple[bool, DispatchRecord]:
        if not key:
            raise ValueError("Dispatch key required")
        with self.connect() as db:
            added = (
                db.execute(
                    "INSERT OR IGNORE INTO dispatch(key,state) VALUES (?, 'reserved')",
                    (key,),
                ).rowcount
                == 1
            )
            row = db.execute(
                "SELECT key,state,call_id,call_status FROM dispatch WHERE key=?", (key,)
            ).fetchone()
        return added, DispatchRecord(*row)

    def finish(self, key: str, state: str, call_id: str | None, call_status: str):
        if state not in ("placed", "failed", "unknown"):
            raise ValueError("Invalid dispatch state")
        with self.connect() as db:
            updated = db.execute(
                "UPDATE dispatch SET state=?,call_id=?,call_status=? WHERE key=? AND state=?",
                (state, call_id, call_status, key, "reserved"),
            ).rowcount
            if updated != 1:
                raise ValueError("Dispatch has no unfinished reservation")
