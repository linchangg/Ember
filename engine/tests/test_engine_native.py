"""The real orchestrator on Abby's Rust HazardModel (docs/RUST_HAZARD_NEXT_TASKS.md item 1).

precompute -> Loop -> ModelHost -> hazard.factory.create_hazard_model(backend="rust"), dev profile
(16 members), filter pf, over test_engine.py's SYNTHETIC scenario (60x60 cells at 150 m, flat GR2,
synthetic GOES pixels) with two SYNTHETIC HRRR files read through NcForcing/weather_from_hrrr.

Needs a built fire-bridge: CI builds it (.github/workflows/hazard-backend.yml). Otherwise the
module is skipped; the lookup is Abby's own BackendOptions.executable() (EMBERLINE_RUST_BRIDGE
or physics/surface_v1/target/release/fire-bridge[.exe]).
"""

from __future__ import annotations

import asyncio
import json
import shutil

import pytest
from hazard_standin import flat_landscape
from test_engine import (MIN, SCN, DoctoredSource, _arrival, _key, check_delayed_report, physics_ks, synth_source,
                         write_hrrr, write_scenario)

from emberline.contracts import CycleBundle
from emberline.hazard.api import MODEL_VERSION
from emberline.hazard.bridge import BackendOptions
from emberline.runtime import cache
from emberline.runtime.loop import NcForcing, load_scenario_dir
from emberline.runtime.model_host import model_grid, rust_backend
from emberline.scripts.precompute import precompute


def _bridge_missing() -> str | None:
    try:
        BackendOptions().executable()
    except RuntimeError as e:
        return str(e)
    return None


MISSING = _bridge_missing()
pytestmark = pytest.mark.skipif(MISSING is not None, reason=f"fire-bridge not built ({MISSING})")
N_PHYS = 13  # k0..k12: checkpoints at k0, k6, k12


def run_native(ctx, root, source=None):
    make, identity = rust_backend()
    forcing = NcForcing(ctx.scn_dir / "forcing", model_grid(ctx))
    return asyncio.run(precompute(ctx, source or synth_source(ctx), forcing, make, flat_landscape(ctx), root, "main",
                                  "pf", members_profile="dev", identity=identity))


@pytest.fixture(scope="module")
def native(tmp_path_factory):
    """Two independent dev-profile precomputes of the same synthetic replay."""
    base = tmp_path_factory.mktemp("native")
    ctx = load_scenario_dir(write_scenario(base / "scenarios", n_phys=N_PHYS))
    write_hrrr(ctx, ctx.scn_dir / "forcing")
    a, b = base / "a", base / "b"
    return ctx, a, b, [run_native(ctx, a), run_native(ctx, b)]


def _bundle(root, k) -> CycleBundle:
    return CycleBundle.model_validate_json((cache.cycle_dir(root, SCN, "main", k) / "bundle.json").read_text("utf-8"))


def _physics(root, k) -> dict:
    return json.loads((cache.cycle_dir(root, SCN, "main", k) / "physics.json").read_text("utf-8"))


def test_native_precompute_through_ignition(native):
    ctx, a, _, (loop, _) = native
    ks = physics_ks(ctx)
    assert len(ks) == N_PHYS >= 12
    index = json.loads((a / SCN / "main" / "index.json").read_text("utf-8"))
    assert [c["knowledge_sim_ms"] for c in index["cycles"]] == ctx.cycles()  # 3 pre-ignition + 13 physics
    assert all(_bundle(a, k).cycle is None for k in ctx.cycles() if k < ctx.t_ign_ms)
    assert loop.host.restored_from == []  # one in-memory model, stepped once per cycle
    assimilated = 0
    for k in ks:
        b = _bundle(a, k)
        c = b.cycle
        assert c is not None and c.source == "live" and c.filter_mode == "pf"
        assert c.model_version == MODEL_VERSION + "+dev-n16" and c.n_members == 16  # never passes as the reported run
        assert 1 <= c.ess <= 16 + 1e-9 and 0 <= c.tempering_alpha <= 1 and c.burned_ha >= 0
        assert len(b.zone_risk) == 4
        raw = _arrival(a, k)
        assert len(raw) == 5 * 60 * 60 and any(1 <= v <= 254 for v in raw)  # forecast leads inside the horizon
        assimilated += sum(o.status == "assimilated" for o in b.observations)
        assert all(o.available_sim_ms <= k for o in b.observations)
    assert assimilated > 0  # the synthetic hot pixels went through the particle filter
    assert _bundle(a, ks[-1]).cycle.burned_ha > 0 and 0 in _arrival(a, ks[-1])  # an hour after ignition

    # Forcing: release-time audit per cycle; the older init until B's f00 is out, then B's released bands only.
    audits = [_physics(a, k)["forcing"] for k in ks]
    assert audits[0]["file"] == audits[1]["file"] != audits[2]["file"] == audits[-1]["file"]
    assert audits[0]["selected_fxx"] == list(range(10)) and audits[7]["selected_fxx"] == [0]
    assert audits[8]["selected_fxx"] == [0, 1, 2]
    for k, au in zip(ks, audits, strict=True):
        assert max(au["available_ms_by_fxx"]) <= k and len(au["sha256"]) == 64
    assert audits[8]["valid_min_range"][1] > (ks[8] - ctx.t_ref_ms) / MIN  # a band valid after the issue: legal
    ident = _physics(a, ks[0])["identity"]  # which build, catalog and config produced the cycle
    assert ident["run_hash"] == loop.config_hash and len(ident["physics_sha256"]) == len(ident["catalog_sha256"]) == 64


def test_native_delayed_report(native, tmp_path):
    """Gate item 'delayed report' (RUST_HAZARD_NEXT_TASKS item 1). Backward seek, branch creation and
    parent/identical-branch bit-identity are still open wave-3 gate items."""
    ctx, a, _, _ = native
    check_delayed_report(ctx, a, tmp_path / "late", lambda root, src: run_native(ctx, root, src))


def test_native_determinism(native):
    ctx, a, b, _ = native
    for k in physics_ks(ctx):
        assert _key(a, k) == _key(b, k)
        assert _arrival(a, k) == _arrival(b, k), k


def test_native_restore_from_checkpoint_is_byte_identical(native, tmp_path):
    ctx, a, _, _ = native
    ks = physics_ks(ctx)
    c = tmp_path / "c"
    shutil.copytree(a, c)
    shutil.rmtree(cache.cycle_dir(c, SCN, "main", ks[9]))  # first cycle after the k8 weather change + 1
    loop = run_native(ctx, c)
    assert loop.host.restored_from == [ks[6]]  # Rust checkpoint at k6, rolled through k7, k8 (weather update)
    assert _arrival(c, ks[9]) == _arrival(a, ks[9])
    assert _key(c, ks[9]) == _key(a, ks[9])
    assert _physics(c, ks[9])["forcing"] == _physics(a, ks[9])["forcing"]


def test_native_no_future_data(native, tmp_path):
    ctx = native[0]
    ks = physics_ks(ctx)

    class LateDoctor(DoctoredSource):  # honest until two physics cycles have run, then one record from the future
        def collect(self, a_ms, b_ms):
            return self.inner.collect(a_ms, b_ms) if b_ms < ks[2] else super().collect(a_ms, b_ms)

    root = tmp_path / "doctored"
    with pytest.raises(AssertionError, match="future data"):
        run_native(ctx, root, LateDoctor(synth_source(ctx)))
    assert [(cache.cycle_dir(root, SCN, "main", k) / "physics.json").exists() for k in ks[:3]] == [True, True, False]
