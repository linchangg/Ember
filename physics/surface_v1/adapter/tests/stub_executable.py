"""Stand-in executable for bridge failure tests. Not a fire model."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True)
    parser.add_argument("--pid-file")
    parser.add_argument("--argv-file")
    parser.add_argument("rest", nargs="*")
    args = parser.parse_args()
    if args.pid_file:
        Path(args.pid_file).write_text(str(os.getpid()), encoding="utf-8")
    if args.mode == "sleep":
        time.sleep(30)
        return 0
    if args.mode == "dump-argv":
        if not args.argv_file:
            return 2
        Path(args.argv_file).write_text(json.dumps(sys.argv), encoding="utf-8")
        return 0
    if args.mode == "validation":
        print(
            json.dumps(
                {
                    "code": "SchemaMismatch",
                    "message": "fixture validation failure",
                    "artifact": None,
                    "cell_index": None,
                    "time_s": None,
                }
            ),
            file=sys.stderr,
        )
        return 2
    if args.mode == "numerical":
        print(
            json.dumps(
                {
                    "code": "NumericalOverflow",
                    "message": "fixture numerical failure",
                    "artifact": None,
                    "cell_index": None,
                    "time_s": None,
                }
            ),
            file=sys.stderr,
        )
        return 3
    if args.mode == "cancel":
        print(
            json.dumps(
                {
                    "code": "Cancelled",
                    "message": "fixture cancellation",
                    "artifact": None,
                    "cell_index": None,
                    "time_s": None,
                }
            ),
            file=sys.stderr,
        )
        return 130
    if args.mode == "memory":
        print(
            json.dumps(
                {
                    "code": "MemoryBudgetExceeded",
                    "message": "fixture memory budget",
                    "artifact": None,
                    "cell_index": None,
                    "time_s": None,
                }
            ),
            file=sys.stderr,
        )
        return 4
    if args.mode == "malformed":
        print("not json", file=sys.stderr)
        return 1
    print(f"unknown mode {args.mode}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
