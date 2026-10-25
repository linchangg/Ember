"""Per-zone lead time: Ember's first warn/order alert vs the official Genasys alert.

    uv run python -m ember.scripts.lead_times --cache-root <assets>/cache --scenario eaton \
        --branches main,satellites-only,openloop

Reads <cache-root>/<scn>/<branch>/index.json + bundles (the precompute layout; an <assets> dir that holds
cache/<scn>/ also works) and data/scenarios/<scn>/truth/zone_orders.json. Writes
<cache-root>/<scn>/lead_times.json and prints a table. Lead = official − ours, in minutes:
positive means Ember alerted earlier, negative later. Reported as is, even when negative.
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime
from pathlib import Path

from ember.data.config import scenario_dir
from ember.risk.alerts import PST
from ember.runtime import cache

DEFINITION = (
    "An Ember alert is 'issued' for a zone at the triggered_sim_ms (the cycle's knowledge time) of the first "
    "Alert in any bundle whose zone_ids contain the zone, in any state (precompute has no duty officer, so every "
    "alert is a proposal; a later 'superseded' copy keeps its original triggered_sim_ms). 'warn' = first alert at "
    "level warning OR order_recommended (zone first reached >= warning); 'order' = first order_recommended. "
    "Official: 'warn' = first Genasys warning OR order for the zone, 'order' = first order (advisories excluded). "
    "lead_min = (official - ours) / 60000: positive = Ember earlier, negative = later, null = no alert in the "
    "cached cycles (see cache_through_pst). Ember times are quantized to 5-min cycles; official times to the "
    "minute. This compares alert times only; it says nothing about outcomes."
)
LEVELS = ("warn", "order")
# Ember levels (contracts.Alert) and official levels (zone_orders.json) -> which firsts they set.
KEYS_BY_LEVEL = {"warning": ("warn",), "order_recommended": ("warn", "order"), "order": ("warn", "order")}


def pst(ms: int | None) -> str | None:
    return None if ms is None else datetime.fromtimestamp(ms / 1000, PST).strftime("%m-%d %H:%M")


def first_times(alerts: list[tuple[str, int, list[str]]]) -> dict[str, dict[str, int]]:
    """[(level, t_ms, zone_ids)] -> {zone: {"warn": ms, "order": ms}}. Order counts as >= warn."""
    first: dict[str, dict[str, int]] = {}
    for level, t, zones in alerts:
        keys = KEYS_BY_LEVEL.get(level, ())  # advisories count for neither
        for z in zones:
            for key in keys:
                f = first.setdefault(z, {})
                f[key] = min(f.get(key, t), t)
    return first


def lead_min(official: int | None, ours: int | None) -> float | None:
    return None if official is None or ours is None else (official - ours) / 60_000


def load_branch(root: Path, scn: str, branch: str) -> list[dict] | None:
    index = root / scn / branch / "index.json"
    if not index.exists():
        return None
    ks = [c["knowledge_sim_ms"] for c in json.loads(index.read_text(encoding="utf-8"))["cycles"]]
    return [json.loads((cache.cycle_dir(root, scn, branch, k) / "bundle.json").read_text(encoding="utf-8"))
            for k in sorted(ks)]


def summarize(rows: list[dict]) -> dict:
    out = {}
    for key in LEVELS:
        leads = [r[f"{key}_lead_min"] for r in rows if r[f"{key}_lead_min"] is not None]
        out[key] = {
            "n_zones": len(rows),
            "n_earlier": sum(x > 0 for x in leads),
            "n_same_minute": sum(x == 0 for x in leads),
            "n_later": sum(x < 0 for x in leads),
            "n_no_alert": len(rows) - len(leads),
            "median_lead_min": statistics.median(leads) if leads else None,
        }
    return out


def compute(truth: dict, branches: dict[str, list[dict] | None]) -> dict:
    official = first_times([(e["official_level"], e["t_sim_ms"], [e["zone_id"]]) for e in truth["entries"]])
    west = truth["west_altadena_0325_zone_ids"]
    result: dict = {"definition": DEFINITION, "sources": truth["sources"], "zone_orders": "truth/zone_orders.json",
                    "branches": {}, "skipped": []}
    for name, bundles in branches.items():
        if not bundles:
            result["skipped"].append({"branch": name, "reason": "no index.json or no cycles in the cache"})
            continue
        ours = first_times([(a["level"], a["triggered_sim_ms"], a["zone_ids"]) for b in bundles for a in b["alerts"]])
        through = bundles[-1]["knowledge_sim_ms"]
        zones = {}
        for z in sorted(official):
            row = {}
            for key in LEVELS:
                o, e = official[z].get(key), ours.get(z, {}).get(key)
                row |= {f"official_{key}_sim_ms": o, f"official_{key}_pst": pst(o), f"{key}_sim_ms": e,
                        f"{key}_pst": pst(e), f"{key}_lead_min": lead_min(o, e)}
            zones[z] = row
        result["branches"][name] = {
            "cache_through_sim_ms": through,
            "cache_through_pst": pst(through),
            "n_cycles": len(bundles),
            "zones": zones,
            "west_altadena_0325_summary": summarize([zones[z] for z in west]),
            "alerted_without_official": sorted(set(ours) - set(official)),
        }
    return result


def _cell(ms_pst: str | None, lead: float | None) -> str:
    return "-" if ms_pst is None else f"{ms_pst[6:]} ({lead:+.0f})" if lead is not None else ms_pst[6:]


def print_table(result: dict, west: list[str]) -> None:
    print(DEFINITION, "\n")
    for name, br in result["branches"].items():
        print(f"== {name}: {br['n_cycles']} cycles through {br['cache_through_pst']} PST; "
              "cells are ours (lead min, + = earlier)")
        print(f"{'zone':18} {'off warn':>8} {'ours warn':>14} {'off order':>9} {'ours order':>14}")
        for z, r in br["zones"].items():
            mark = "*" if z in west else " "
            print(f"{z:17}{mark} {(r['official_warn_pst'] or '-')[6:]:>8} {_cell(r['warn_pst'], r['warn_lead_min']):>14}"
                  f" {(r['official_order_pst'] or '-')[6:]:>9} {_cell(r['order_pst'], r['order_lead_min']):>14}")
        for key, s in br["west_altadena_0325_summary"].items():
            print(f"  west-Altadena 03:25 zones (*), {key}: earlier {s['n_earlier']}, same {s['n_same_minute']}, "
                  f"later {s['n_later']}, no alert {s['n_no_alert']}, median lead {s['median_lead_min']} min")
        print(f"  alerted, no official warning/order: {', '.join(br['alerted_without_official']) or 'none'}\n")
    for s in result["skipped"]:
        print(f"skipped {s['branch']}: {s['reason']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Per-zone lead time vs official Genasys alerts.")
    ap.add_argument("--cache-root", type=Path, default=cache.DEFAULT_ROOT)
    ap.add_argument("--scenario", default="eaton")
    ap.add_argument("--branches", default="main,satellites-only,openloop")
    ap.add_argument("--zone-orders", type=Path, default=None, help="default: data/scenarios/<scn>/truth/zone_orders.json")
    a = ap.parse_args()
    root = a.cache_root
    if not (root / a.scenario).exists() and (root / "cache" / a.scenario).exists():
        root = root / "cache"  # an <assets> dir was passed
    truth = json.loads((a.zone_orders or scenario_dir(a.scenario) / "truth" / "zone_orders.json")
                       .read_text(encoding="utf-8"))
    names = [b.strip() for b in a.branches.split(",") if b.strip()]
    result = compute(truth, {b: load_branch(root, a.scenario, b) for b in names})
    print_table(result, truth["west_altadena_0325_zone_ids"])
    out = root / a.scenario / "lead_times.json"
    out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
