"""Choose a development profile from completed benchmark reports.

Decisions use stored timing samples. ``eligible_for_profile_selection`` is ignored.
A missing ``latency_budgets`` block is derived from validated samples, or the
report is marked insufficient. This module does not change production defaults
and does not award forecast skill.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .benchmark import BenchmarkConfig, median, nearest_rank_p95, physical_extent
from .experiment_io import (
    ConfigError,
    ExecutionError,
    canonical_json,
    read_strict_json,
    repo_root,
    sha256_canonical,
    write_strict_json,
)

DECLARED_CANDIDATES = (
    "config/hazard_experiments/benchmark-smoke.json",
    "config/hazard_experiments/benchmark-target.json",
)
REQUIRED_SAMPLE_FIELDS = (
    "workload",
    "category",
    "elapsed_ns",
    "timeout",
    "error",
    "python_peak_rss_bytes",
    "child_peak_rss_bytes",
)
VERIFICATION_FIELDS = {
    "checkpoint_next_cycle_parity": "checkpoint next-cycle parity",
    "canonical_export_validation": "canonical export validation",
}


def identify_report(report: dict) -> dict[str, object]:
    samples = report.get("samples")
    schema = report.get("schema_version")
    return {
        "schema_version": schema if isinstance(schema, int) and not isinstance(schema, bool) else None,
        "experiment_kind": report.get("experiment_kind"),
        "run_id": report.get("run_id"),
        "latency_budgets_present": isinstance(report.get("latency_budgets"), dict),
        "sample_count": len(samples) if isinstance(samples, list) else None,
        "version_label": (
            "benchmark-schema-1-samples" if schema == 1 else "unrecognized-benchmark-report"
        ),
    }


def _sample_error(sample: object) -> str | None:
    if not isinstance(sample, dict):
        return "sample is not an object"
    for field in REQUIRED_SAMPLE_FIELDS:
        if field not in sample:
            return f"sample is missing {field}"
    if not isinstance(sample["workload"], str) or not isinstance(sample["category"], str):
        return "sample workload or category is not text"
    elapsed = sample["elapsed_ns"]
    if elapsed is not None and (isinstance(elapsed, bool) or not isinstance(elapsed, int) or elapsed < 0):
        return "sample elapsed_ns must be a nonnegative integer or null"
    if not isinstance(sample["timeout"], bool):
        return "sample timeout must be a boolean"
    if sample["error"] is not None and not isinstance(sample["error"], str):
        return "sample error must be text or null"
    for field in ("python_peak_rss_bytes", "child_peak_rss_bytes"):
        value = sample[field]
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            return f"sample {field} must be a nonnegative integer or null"
    return None


def _series_stats(samples: list[dict]) -> dict[str, dict[str, object]]:
    grouped: dict[tuple[str, str], list[dict]] = {}
    for sample in samples:
        if sample["category"] not in {"cold", "warm"}:
            continue
        grouped.setdefault((sample["workload"], sample["category"]), []).append(sample)
    stats: dict[str, dict[str, object]] = {}
    for workload, category in sorted(grouped):
        rows = grouped[(workload, category)]
        finished = [
            row for row in rows if row["timeout"] is False and row["error"] is None and isinstance(row["elapsed_ns"], int)
        ]
        elapsed = [row["elapsed_ns"] / 1e6 for row in finished]
        p95, label = nearest_rank_p95(elapsed)
        stats[f"{workload}:{category}"] = {
            "count": len(rows),
            "finished_count": len(finished),
            "timeout_count": sum(row["timeout"] is True for row in rows),
            "median_elapsed_ms": median(elapsed),
            "p95_elapsed_ms": p95,
            "p95_label": label,
        }
    return stats


def _memory(samples: list[dict]) -> dict[str, object]:
    python_peaks = [row["python_peak_rss_bytes"] for row in samples if isinstance(row["python_peak_rss_bytes"], int)]
    child_peaks = [row["child_peak_rss_bytes"] for row in samples if isinstance(row["child_peak_rss_bytes"], int)]
    return {
        "python_peak_rss_bytes": max(python_peaks) if python_peaks else None,
        "native_child_peak_rss_bytes": max(child_peaks) if child_peaks else None,
        "peaks_are_independent": True,
    }


def _budgets_match(stored: dict, derived: dict[str, dict[str, object]]) -> bool:
    forecasts = stored.get("forecasts")
    if not isinstance(forecasts, dict):
        return False
    if set(forecasts) != set(derived):
        return False
    for name, stats in derived.items():
        recorded = forecasts.get(name)
        if not isinstance(recorded, dict):
            return False
        left = recorded.get("median_elapsed_ms")
        right = stats["median_elapsed_ms"]
        if left is None or right is None:
            if left != right:
                return False
            continue
        if not isinstance(left, (int, float)) or isinstance(left, bool):
            return False
        if abs(float(left) - float(right)) > 0.05:
            return False
    return True


def load_declared_candidates(root: Path | None = None) -> list[BenchmarkConfig]:
    base = Path(root or repo_root())
    found = []
    for relative in DECLARED_CANDIDATES:
        found.append(BenchmarkConfig.model_validate(read_strict_json(base / relative)))
    return found


def _matches_declared(config: BenchmarkConfig, declared: BenchmarkConfig) -> bool:
    return config.model_dump(mode="json") == declared.model_dump(mode="json")


def evaluate_report(report: dict, declared: list[BenchmarkConfig]) -> dict[str, object]:
    """Evaluate one report from its samples. The execution-eligibility flag is not read."""
    identity = identify_report(report)
    reasons: list[str] = []
    if type(report.get("schema_version")) is not int or report.get("schema_version") != 1:
        reasons.append("benchmark report schema is unsupported")
    if report.get("experiment_kind") != "benchmark":
        reasons.append("report is not a benchmark experiment")
    if report.get("status") != "complete":
        reasons.append("benchmark report is not complete")
    content_hash = report.get("content_sha256")
    comparable = dict(report)
    comparable.pop("content_sha256", None)
    observed_hash = sha256_canonical(comparable)
    if isinstance(content_hash, str) and content_hash != observed_hash:
        reasons.append("report content hash does not match its samples")
    samples = report.get("samples")
    derived: dict[str, dict[str, object]] | None = None
    latency_source = "insufficient"
    if not isinstance(samples, list) or any(_sample_error(sample) for sample in samples):
        reasons.append("timing samples are missing or invalid")
    else:
        derived = _series_stats(samples)
        latency_source = "validated_samples"
        if any(sample["timeout"] or sample["error"] for sample in samples):
            reasons.append("a stored sample records a timeout or child error")
    config: BenchmarkConfig | None = None
    try:
        config = BenchmarkConfig.model_validate(report.get("resolved_config"))
    except Exception:
        reasons.append("resolved config is missing or incompatible")
    matched = None
    if config is not None:
        matched = next((item for item in declared if _matches_declared(config, item)), None)
        if matched is None:
            reasons.append("profile dimensions were not declared before benchmarking")
    if derived is not None and config is not None:
        for workload in config.workloads:
            for category in ("cold", "warm"):
                name = f"{workload}:{category}"
                stats = derived.get(name)
                if stats is None or stats["finished_count"] == 0:
                    reasons.append(f"{name} did not complete")
                    continue
                median_ms = stats["median_elapsed_ms"]
                p95 = stats["p95_elapsed_ms"]
                if isinstance(median_ms, float) and median_ms > config.interactive_budget_ms:
                    reasons.append(
                        f"{name} median_elapsed_ms {median_ms:.3f} exceeds interactive budget "
                        f"{config.interactive_budget_ms}"
                    )
                if isinstance(p95, float) and p95 > config.interactive_budget_ms:
                    reasons.append(
                        f"{name} outer p95_elapsed_ms {p95:.3f} exceeds interactive budget "
                        f"{config.interactive_budget_ms}"
                    )
        memory = _memory(samples)
        limit = config.memory_budget_bytes
        for label, peak in (
            ("python", memory["python_peak_rss_bytes"]),
            ("native child", memory["native_child_peak_rss_bytes"]),
        ):
            if not isinstance(peak, int):
                reasons.append(f"{label} peak RSS was not recorded")
            elif peak > limit:
                reasons.append(f"{label} peak RSS {peak} exceeds per-process limit {limit}")
    stored_budgets = report.get("latency_budgets")
    if isinstance(stored_budgets, dict) and derived is not None and not _budgets_match(stored_budgets, derived):
        reasons.append("latency_budgets does not match validated samples")
    verification = report.get("verification") if isinstance(report.get("verification"), dict) else {}
    for field, label in VERIFICATION_FIELDS.items():
        if verification.get(field) != "passed":
            reasons.append(f"{label} was not recorded as passed")
    provisional = False
    if derived is not None and any(
        isinstance(stats["finished_count"], int) and stats["finished_count"] < 20 for stats in derived.values()
    ):
        provisional = True
    eligible = not reasons
    extent = physical_extent(config) if config is not None else None
    worst = None
    if derived:
        p95s = [stats["p95_elapsed_ms"] for stats in derived.values() if isinstance(stats["p95_elapsed_ms"], float)]
        worst = max(p95s) if p95s else None
    return {
        "run_id": report.get("run_id"),
        "report_sha256": observed_hash,
        "report_version": identity,
        "resolved_config": None if config is None else config.model_dump(mode="json"),
        "resolved_config_sha256": None if config is None else sha256_canonical(config.model_dump(mode="json")),
        "physical_extent": extent,
        "latency_source": latency_source,
        "series": derived or {},
        "eligible": eligible,
        "provisional": provisional and eligible,
        "reasons": reasons,
        "worst_outer_p95_ms": worst,
        "scientific_acceptance": "not_established_by_performance",
    }


def _rank_key(candidate: dict) -> tuple:
    extent = candidate["physical_extent"] or {}
    area = float(extent.get("east_m") or 0) * float(extent.get("north_m") or 0)
    cell = float(extent.get("cell_m") or 1e18)
    members = int(extent.get("members") or 0)
    worst = candidate["worst_outer_p95_ms"]
    worst_value = float(worst) if isinstance(worst, (int, float)) else 1e18
    return (-area, cell, -members, worst_value, str(candidate.get("run_id") or ""))


def select_profiles(reports: list[dict], declared: list[BenchmarkConfig] | None = None) -> dict[str, object]:
    catalog = declared if declared is not None else load_declared_candidates()
    evaluated = [evaluate_report(report, catalog) for report in reports]
    evaluated.sort(key=lambda item: (str(item.get("run_id")), str(item.get("report_sha256"))))
    eligible = [item for item in evaluated if item["eligible"]]
    ordered = sorted(eligible, key=_rank_key)
    selected = ordered[0] if ordered else None
    failed = []
    if selected is None:
        for item in evaluated:
            failed.append({"run_id": item["run_id"], "reasons": item["reasons"]})
    return {
        "schema_version": 1,
        "selected_profile": None if selected is None else selected["run_id"],
        "execution_mode": "measured_development" if selected is not None else "cached_replay",
        "provisional": bool(selected and selected["provisional"]),
        "scientific_acceptance": "not_established_by_performance",
        "candidates": evaluated,
        "failed_reasons": failed,
        "ranking_order": [
            "larger physical coverage",
            "finer cell size",
            "more members",
            "lower worst-workload outer p95",
        ],
        "ignored_fields": ["summary.eligible_for_profile_selection"],
    }


def write_selection(path: Path, decision: dict) -> None:
    if path.exists():
        raise ConfigError(f"selection output already exists: {path}")
    write_strict_json(path, decision)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        reports = [read_strict_json(path) for path in args.report]
        if any(not isinstance(report, dict) for report in reports):
            raise ConfigError("benchmark report must be a JSON object")
        decision = select_profiles(reports)
        write_selection(args.out, decision)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (OSError, ExecutionError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(args.out, decision["selected_profile"], decision["execution_mode"], flush=True)
    return 0


def canonical_decision(decision: dict) -> str:
    return canonical_json(decision)


if __name__ == "__main__":
    raise SystemExit(main())
