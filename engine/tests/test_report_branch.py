import asyncio
from types import SimpleNamespace
import numpy as np
import pytest
from emberline.contracts import Report
from emberline.runtime.branch import BranchSource, BranchManager, report_observation
from test_engine import (
    write_scenario,
    synth_source,
    FlatForcing,
    run_precompute,
    MIN,
    SCN,
)
from emberline.runtime.loop import load_scenario_dir, Loop
from emberline.runtime.clock import FastClock
from emberline.runtime.model_host import make_host
from emberline.sync.cache_sink import CacheSink
from hazard_standin import FakeHazardModel, flat_landscape


def report(ctx, **kw):
    from emberline.data.grid import xy_to_lonlat

    g = ctx.grid
    lon, lat = xy_to_lonlat(g["x0"] + 20 * g["cell_m"], g["y0"] - 20 * g["cell_m"], g)
    values = dict(
        report_id="rep_test0001",
        scenario_id=ctx.scenario_id,
        branch_id="main",
        person_id=None,
        channel="tap",
        kind="fire",
        summary="synthetic test",
        lon=lon,
        lat=lat,
        radius_m=250,
        trust=0.5,
        observed_sim_ms=ctx.k0_ms,
        created_wall_ms=100000,
    )
    values.update(kw)
    return Report(**values)


def test_report_acceptance_rejects_future_outside_and_rate_limit(tmp_path):
    ctx = load_scenario_dir(write_scenario(tmp_path / "scenarios", n_phys=4))
    r = report(ctx)
    assert report_observation(r, ctx, ctx.k0_ms, [])[0].trust == 0.5
    assert (
        report_observation(report(ctx, kind="smoke"), ctx, ctx.k0_ms, [])[0].trust
        == 0.25
    )
    assert report_observation(
        report(ctx, observed_sim_ms=ctx.k0_ms + 1), ctx, ctx.k0_ms, []
    )[1]
    assert report_observation(report(ctx, lon=0), ctx, ctx.k0_ms, [])[1]
    prior = report(ctx, report_id="rep_prior", person_id="per_a", created_wall_ms=99999)
    assert report_observation(report(ctx, person_id="per_a"), ctx, ctx.k0_ms, [prior])[
        1
    ]


def test_report_at_exact_fork_releases_next_cycle(tmp_path):
    ctx = load_scenario_dir(write_scenario(tmp_path / "scenarios", n_phys=3))
    obs, _ = report_observation(report(ctx), ctx, ctx.k0_ms, [])
    source = BranchSource(
        SimpleNamespace(collect=lambda a, b: ([], [])),
        [obs],
        ctx.start_ms,
        ctx.cycle_ms,
    )
    assert source.collect(ctx.k0_ms, ctx.k0_ms + ctx.cycle_ms)[0] == [obs]
    assert (
        source.collect(ctx.k0_ms + ctx.cycle_ms, ctx.k0_ms + 2 * ctx.cycle_ms)[0] == []
    )


def test_real_loop_fork_preserves_parent_and_injects_only_child(tmp_path):
    ctx = load_scenario_dir(write_scenario(tmp_path / "scenarios", n_phys=5))
    root = tmp_path / "cache"
    run_precompute(ctx, root)
    parent_file = root / SCN / "main" / str(ctx.k0_ms) / "bundle.json"
    parent_bytes = parent_file.read_bytes()
    forcing = FlatForcing(ctx)
    host = make_host(ctx, FakeHazardModel, flat_landscape(ctx), forcing, root)
    sink = CacheSink(root, ctx.zones)
    loop = Loop(
        ctx,
        sink,
        FastClock(),
        synth_source(ctx),
        host,
        root,
        "main",
        "synthetic",
        stop_at_end=True,
    )
    # Stand-in has no native arrivals; fire reports don't need the all-clear gate.
    r = report(ctx)

    async def run():
        manager = BranchManager(loop)
        result = await manager.accept(
            r, sink, SimpleNamespace(sim_now=lambda: ctx.k0_ms), [], []
        )
        assert result["branch_id"] == "b1"
        assert loop.branch == "b1" and host.branch == "b1"
        # Repeating the same command must not allocate b2.
        assert (
            await BranchManager(loop).accept(
                r, sink, SimpleNamespace(sim_now=lambda: ctx.k0_ms), [], []
            )
            == result
        )
        await loop.run()

    asyncio.run(run())
    assert parent_file.read_bytes() == parent_bytes
    import json

    child = json.loads(
        (root / SCN / "b1" / str(ctx.k0_ms + ctx.cycle_ms) / "bundle.json").read_text()
    )
    assert any(x["obs_id"] == "obs_rep_rep_test0001" for x in child["observations"])
    assert child["reports"][0]["status"] == "accepted"
    assert len(BranchManager(loop).records("b1")) == 1


def test_native_report_branch_and_parent_checkpoint(tmp_path):
    from emberline.hazard.bridge import BackendOptions
    from emberline.runtime.model_host import rust_backend
    from emberline.scripts.precompute import precompute

    try:
        BackendOptions().executable()
    except RuntimeError:
        pytest.skip("native bridge not built")
    ctx = load_scenario_dir(write_scenario(tmp_path / "scenarios", n_phys=3))
    ctx.cfg["n_members"] = 2
    root = tmp_path / "native-cache"
    forcing = FlatForcing(ctx)
    make, identity = rust_backend()
    loop = asyncio.run(
        precompute(
            ctx,
            synth_source(ctx),
            forcing,
            make,
            flat_landscape(ctx),
            root,
            "main",
            "pf",
            identity=identity,
        )
    )
    parent_files = {
        p: p.read_bytes() for p in (root / SCN / "main").glob("*/physics.json")
    }

    async def branch():
        loop.i = 0
        result = await BranchManager(loop).accept(
            report(ctx), loop.sink, SimpleNamespace(sim_now=lambda: ctx.k0_ms), [], []
        )
        assert result["accepted"]
        await loop.run()

    asyncio.run(branch())
    assert all(p.read_bytes() == raw for p, raw in parent_files.items())
    import json

    bundle = json.loads(
        (root / SCN / "b1" / str(ctx.k0_ms + ctx.cycle_ms) / "bundle.json").read_text()
    )
    assert bundle["cycle"]["model_version"] == "rust-levelset-pf-1"
    assert bundle["reports"][0]["status"] == "accepted"
    assert any(o["obs_id"] == "obs_rep_rep_test0001" for o in bundle["observations"])


def test_lost_branch_ack_reuses_reserved_branch(tmp_path):
    ctx = load_scenario_dir(write_scenario(tmp_path / "scenarios", n_phys=3))
    root = tmp_path / "cache"
    run_precompute(ctx, root)
    forcing = FlatForcing(ctx)
    loop = Loop(
        ctx,
        CacheSink(root, ctx.zones),
        FastClock(),
        synth_source(ctx),
        make_host(ctx, FakeHazardModel, flat_landscape(ctx), forcing, root),
        root,
        "main",
        "synthetic",
        stop_at_end=True,
    )

    class LostAck(CacheSink):
        async def create_branch(self, payload):
            raise ConnectionError("simulated lost acknowledgment")

    r = report(ctx)

    async def run():
        manager = BranchManager(loop)
        with pytest.raises(ConnectionError):
            await manager.accept(
                r, LostAck(root), SimpleNamespace(sim_now=lambda: ctx.k0_ms), [], []
            )
        saved = BranchManager(loop)
        assert list(saved.data["branches"]) == ["b1"]
        existing = {
            k: v for k, v in saved.data["branches"]["b1"].items() if k != "observation"
        }
        # The normal poller can discover the hub's active child after a lost ACK,
        # without the caller manually submitting the report a second time.
        await saved.restore("b1")
        assert loop.report_status_updates[r.report_id].status == "accepted"
        assert saved.data["reports"][r.report_id]["publication"] == "committed"
        result = await saved.accept(
            r, loop.sink, SimpleNamespace(sim_now=lambda: ctx.k0_ms), [], [existing]
        )
        assert result["branch_id"] == "b1" and loop.branch == "b1"
        assert len(saved.data["branches"]) == 1

    asyncio.run(run())


def test_precompute_cycle_limit_preserves_scenario_and_observer_context(tmp_path):
    from emberline.scripts.precompute import precompute

    ctx = load_scenario_dir(write_scenario(tmp_path / "scenario", n_phys=3))
    end = ctx.end_ms
    seen = []

    def observer(k, host, forecast, identity):
        assert all(
            o.available_sim_ms <= k
            for batch in identity["observations_by_issue"].values()
            for o in batch
        )
        assert k in identity["forcing_by_issue"]
        seen.append(k)

    loop = asyncio.run(
        precompute(
            ctx,
            synth_source(ctx),
            FlatForcing(ctx),
            FakeHazardModel,
            flat_landscape(ctx),
            tmp_path / "cache",
            max_cycles=4,
            physics_observer=observer,
        )
    )
    assert ctx.end_ms == end and len(loop.ks) == 4
    assert seen == [ctx.k0_ms]


def test_retryable_report_command_is_not_marked_failed():
    from emberline.sync.poller import Poller
    from emberline.sync.sink import HubSnapshot, PublicationUnavailable

    marked = []

    async def mark(*args):
        marked.append(args)

    loop = SimpleNamespace(ctx=SimpleNamespace(scenario_id="test"))
    poller = Poller(SimpleNamespace(mark_command=mark), None, loop, profile="offline")

    async def unavailable(*args):
        raise PublicationUnavailable("lost branch acknowledgement")

    poller._on_report = unavailable
    asyncio.run(
        poller.dispatch(
            dict(command_id="cmd_test", kind="report", args_json="{}"), HubSnapshot()
        )
    )
    assert marked == []
