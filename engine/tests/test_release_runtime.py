import argparse
import json
import shutil
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from emberline.api.local import LocalState, create_app
from emberline.run import build_loop, FIXTURES
from emberline.runtime.clock import FastClock
from emberline.runtime.loop import BundleReplayLoop
from emberline.sync.cache_sink import CacheSink


@pytest.fixture
def root(tmp_path):
    shutil.copytree(FIXTURES / "static/fixture", tmp_path / "static/fixture")
    shutil.copytree(FIXTURES / "cache/fixture", tmp_path / "cache/fixture")
    return tmp_path


def make(root):
    args = argparse.Namespace(bundle_root=root, fixtures=False, scenario="fixture")
    return build_loop(args, CacheSink(root / "output"), FastClock())[0]


def test_external_bundle_replay_and_host(root):
    loop = make(root)
    assert isinstance(loop, BundleReplayLoop) and loop.host is None
    state = LocalState("fixture", "offline", False, bundle_root=root)
    c = TestClient(create_app(state, None), base_url="http://localhost")
    assert c.get("/static/fixture/manifest.json").status_code == 200
    assert c.get("/cache/fixture/main/index.json").status_code == 200


def test_partial_replay_does_not_report_full_coverage(root):
    path = root / "cache/fixture/main/index.json"
    d = json.loads(path.read_text())
    d["cycles"] = d["cycles"][:2]
    path.write_text(json.dumps(d))
    assert 0 < make(root).status["cache_coverage"] < 1


def test_bundle_replay_rejects_path_escape(root):
    path = root / "cache/fixture/main/index.json"
    d = json.loads(path.read_text())
    d["cycles"][0]["bundle"] = "../secret.json"
    path.write_text(json.dumps(d))
    with pytest.raises(ValueError, match="escapes"):
        make(root)


def test_bundle_replay_rejects_duplicate_issue(root):
    path = root / "cache/fixture/main/index.json"
    d = json.loads(path.read_text())
    d["cycles"].append(d["cycles"][0])
    path.write_text(json.dumps(d))
    with pytest.raises(ValueError, match="Duplicate"):
        make(root)


def test_api_explicit_cache_root(tmp_path):
    (tmp_path / "test.json").write_text('{"isolated":true}')
    state = LocalState("eaton", "offline", False, cache_root=tmp_path)
    c = TestClient(create_app(state, None), base_url="http://localhost")
    assert c.get("/cache/test.json").json() == {"isolated": True}


@pytest.mark.parametrize(
    "private",
    [
        "eaton/dispatch.sqlite3",
        "eaton/branch-evidence.json",
        "eaton/main/checkpoints/state.npz",
    ],
)
def test_private_runtime_state_not_served(tmp_path, private):
    path = tmp_path / private
    path.parent.mkdir(parents=True)
    path.write_bytes(b"private-state")
    state = LocalState("eaton", "offline", False, cache_root=tmp_path)
    c = TestClient(create_app(state, None), base_url="http://localhost")
    assert c.get("/cache/" + private).status_code == 404


def test_bundle_replay_publishes_explicit_report_rejection(root):
    import asyncio
    from emberline.contracts import Report
    from emberline.runtime.branch import BranchManager
    from types import SimpleNamespace

    loop = make(root)
    r = Report(
        report_id="rep_test0001",
        scenario_id="fixture",
        branch_id="main",
        person_id=None,
        channel="tap",
        kind="fire",
        summary="test",
        lon=-118.1,
        lat=34.2,
        radius_m=250,
        trust=0.5,
        observed_sim_ms=loop.ctx.k0_ms,
        created_wall_ms=1000,
    )

    async def run():
        result = await BranchManager(loop).accept(
            r, loop.sink, SimpleNamespace(sim_now=lambda: loop.ctx.k0_ms), [], []
        )
        assert result["accepted"] is False
        await loop._publish_batch([loop.ks[0]])
        bundle = json.loads(
            (
                loop.sink.root / "fixture/main" / str(loop.ks[0]) / "bundle.json"
            ).read_text()
        )
        assert bundle["reports"][0]["status"] == "rejected"
        assert not loop.report_status_updates

    asyncio.run(run())
