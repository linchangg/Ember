import pytest
from emberline.hazard.benchmark_suite import summarize, tree_rss


def test_percentiles_report_all_samples():
    result = summarize([100, 200, 300, 400, 500, 600, 700, 800, 900, 1000])
    assert (
        result["n"] == 10
        and result["p50_ms"] == 550
        and result["p95_ms"] == pytest.approx(955)
    )


@pytest.mark.parametrize("values", [[], [-1], [float("nan")], [float("inf")]])
def test_invalid_measurements_rejected(values):
    with pytest.raises(ValueError):
        summarize(values)


def test_simultaneous_tree_sum_excludes_unrelated_processes():
    rows = [(1, 0, 100), (2, 1, 50), (3, 2, 25), (4, 0, 900)]
    assert tree_rss(rows, 1) == 175 * 1024


def test_late_timeout_retains_partial_results_and_fails_target(monkeypatch):
    from types import SimpleNamespace
    from emberline.hazard import benchmark_suite as suite

    class Model:
        backend_identity = {"physics_sha256": "synthetic-test"}
        native_diagnostics = {"steps": 1}

        def __init__(self, *args):
            self.n = 0

        def init(self, *args):
            pass

        def step(self, *args):
            self.n += 1
            if self.n == 2:
                raise TimeoutError("synthetic timeout")
            return SimpleNamespace(duration_ms=1, ess=2, resampled=False)

    class Memory:
        peak = None
        samples = 0
        error = None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(suite, "RustHazardModel", Model)
    monkeypatch.setattr(suite, "MemorySampler", Memory)
    result = suite.run(members=2, size=3, samples=10, warmup=0)
    assert result["status"] == "failed" and not result["target_met"]
    assert result["completed_samples"] == 1 and len(result["raw"]) == 1
    assert result["failure"]["issue_min"] == 10
    assert result["failure"]["error_type"] == "TimeoutError"
