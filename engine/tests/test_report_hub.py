"""Opt-in end-to-end report command on a fresh disposable local hub database."""

import asyncio
import os
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from hazard_standin import flat_landscape
from test_engine import FlatForcing, synth_source, write_scenario
from test_report_branch import report

from emberline.runtime.clock import FastClock
from emberline.runtime.loop import Loop, load_scenario_dir
from emberline.runtime.model_host import make_host, rust_backend
from emberline.run import load_payload
from emberline.sync.poller import Poller
from emberline.sync.stdb_http import StdbHttp
from emberline.sync.stdb_sink import StdbSink


pytestmark = pytest.mark.skipif(
    not os.environ.get("EMBERLINE_REPORT_HUB_TESTS"), reason="dedicated report hub"
)


def test_native_report_command_publishes_child_and_preserves_parent(tmp_path):
    uri = os.environ.get("STDB_URI", "http://127.0.0.1:3017")
    database = os.environ.get("STDB_DB", "")
    if urlsplit(uri).hostname not in (
        "localhost",
        "127.0.0.1",
    ) or not database.startswith("release-report-"):
        pytest.fail("Use a fresh local release-report-* database")
    ctx = load_scenario_dir(write_scenario(tmp_path / "scenario", n_phys=3))
    ctx.cfg["n_members"] = 2
    ctx.cfg["filter"] = "pf"
    root = tmp_path / "cache"

    async def run():
        hub = StdbHttp(uri, database, None)
        try:
            engine, token = await hub.new_identity()
            service, _ = await hub.new_identity()
            await hub.call("set_identities", engine, service, "test-only-report-pin")
            hub.token = token
            await hub.load_scenario(load_payload(ctx.scn_dir / "web"))
            await hub.call(
                "set_pause_on_proposal", ctx.scenario_id, False, "test-only-report-pin"
            )
            sink = StdbSink(hub, ctx.scenario_id)
            make, identity = rust_backend()
            host = make_host(
                ctx,
                make,
                flat_landscape(ctx),
                FlatForcing(ctx),
                root,
                identity=identity,
            )
            loop = Loop(
                ctx,
                sink,
                FastClock(),
                synth_source(ctx),
                host,
                root,
                "main",
                "rust-levelset-pf-1",
                stop_at_end=True,
            )
            from emberline.hazard.historical_runner import ForecastArchive

            archive = ForecastArchive(tmp_path / "members", {ctx.k0_ms})
            loop.physics_observer = archive
            await loop.run()
            import numpy as np

            with np.load(
                tmp_path / "members" / str(ctx.k0_ms) / "prediction.npz"
            ) as data:
                assert data["arrival_min"].shape[0] == 2
                assert np.isclose(data["weights"].sum(), 1)
            # Branch replay must not overwrite the frozen parent export.
            loop.physics_observer = None
            (before,) = await hub.sql("SELECT * FROM cycle WHERE branch_id = 'main'")
            assert before
            r = report(ctx)
            await hub.submit_report_engine(r.model_dump(mode="json"), "")
            poller = Poller(
                sink,
                SimpleNamespace(sim_now=lambda: ctx.k0_ms),
                loop,
                profile="offline",
            )
            result = await poller._on_report(
                {"report_id": r.report_id}, await sink.poll()
            )
            assert result[0] == "done" and result[1]["accepted"]
            await loop.run()
            after, children, reports, observations = await hub.sql(
                "SELECT * FROM cycle WHERE branch_id = 'main'; "
                "SELECT * FROM cycle WHERE branch_id = 'b1'; "
                f"SELECT * FROM report WHERE report_id = '{r.report_id}'; "
                f"SELECT * FROM observation WHERE obs_id = 'obs_rep_{r.report_id}'"
            )
            assert after == before
            assert children and all(c["knowledge_sim_ms"] > ctx.k0_ms for c in children)
            assert reports[0]["status"] == "accepted"
            assert observations[0]["status"] == "assimilated", observations
            assert observations[0]["available_sim_ms"] == ctx.k0_ms
            assert loop.branch == "b1"
        finally:
            await hub.aclose()

    asyncio.run(run())
