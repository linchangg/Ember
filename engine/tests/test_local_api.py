"""T12 local API: fixture mode, FastAPI TestClient, a fake hub (no network)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ember.api.local import REPO, LocalState, create_app
from ember.sync.stdb_http import ReducerRejected

FIX_MAIN = REPO / "fixtures" / "cache" / "fixture" / "main"
NUMBER = "+15555550100"


class FakeHub:
    def __init__(self, reject: bool = False) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.reject = reject

    async def submit_report_engine(self, report: dict[str, Any], transcript: str) -> None:
        self.calls.append(("submit_report_engine", (report, transcript)))
        if self.reject:
            raise ReducerRejected("submit_report_engine", 530, "nope")

    async def call(self, reducer: str, *args: Any) -> None:
        self.calls.append((reducer, args))


def client(hub: FakeHub | None = None, **kw: Any) -> tuple[TestClient, LocalState]:
    state = LocalState(scenario_id="fixture", profile="offline", fixtures=True, **kw)
    return TestClient(create_app(state, hub), base_url="http://localhost:8787"), state  # type: ignore[arg-type]


def first_bin() -> Path:
    return next(p for p in sorted(FIX_MAIN.glob("*/arrival_q.bin")))


def test_index_and_bundle_served() -> None:
    c, _ = client()
    r = c.get("/cache/fixture/main/index.json")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    index = r.json()
    assert index["scenario_id"] == "fixture"
    bundle_rel = index["cycles"][0]["bundle"]  # ASSET_BASE-relative: cache/fixture/main/{k}/bundle.json
    r = c.get("/" + bundle_rel, headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    assert r.headers.get("content-encoding") == "gzip"
    assert r.json() == json.loads((REPO / "fixtures" / bundle_rel).read_text(encoding="utf-8"))


def test_arrival_bin_bytes() -> None:
    c, _ = client()
    p = first_bin()
    r = c.get(f"/cache/fixture/main/{p.parent.name}/arrival_q.bin", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers.get("content-encoding") == "gzip"
    assert r.content == p.read_bytes()  # httpx decodes gzip


def test_static_served() -> None:
    c, _ = client()
    r = c.get("/static/fixture/manifest.json")
    assert r.status_code == 200 and r.json()["scenario_id"] == "fixture"


@pytest.mark.parametrize(
    "url",
    [
        "/cache/%2e%2e/static/fixture/manifest.json",
        "/cache/fixture/%2E%2E/%2E%2E/static/fixture/manifest.json",
        "/cache/..%5c..%5cstatic%5cfixture%5cmanifest.json",
        "/cache/%2e%2e%2f%2e%2e%2fpyproject.toml",
        "/static/fixture/%2e%2e/%2e%2e/cache/fixture/main/index.json",
        "/static/%2e%2e/cache/fixture/main/index.json",
    ],
)
def test_traversal_blocked(url: str) -> None:
    c, _ = client()
    r = c.get(url)
    assert r.status_code in (400, 404), r.status_code
    assert "scenario_id" not in r.text


def test_absolute_path_blocked() -> None:
    c, _ = client()
    target = (REPO / "fixtures" / "static" / "fixture" / "manifest.json").as_posix()
    r = c.get("/cache//" + target)
    assert r.status_code in (400, 404)


def test_duty_officer_never_echoed(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    c, state = client(FakeHub())
    r = c.post("/dev/duty-officer", json={"number": NUMBER})
    assert r.status_code == 200 and r.json()["masked"] == "•••• 0100"
    assert state.duty_officer_number == NUMBER
    bad = [
        c.post("/dev/duty-officer", json={"number": NUMBER + "x"}),  # not E.164
        c.post("/dev/duty-officer", json={"number": NUMBER, "extra": NUMBER}),
        c.post("/dev/duty-officer", json={"nmbr": NUMBER}),  # 422 path
        c.post("/dev/duty-officer", content=f'{{"number": {NUMBER[1:]}}}', headers={"Content-Type": "application/json"}),
        c.get("/healthz"),
    ]
    assert bad[0].status_code == 400 and bad[2].status_code == 422 and bad[3].status_code == 422
    assert state.duty_officer_number == NUMBER  # the bad one didn't overwrite it
    for resp in [r, *bad]:
        assert NUMBER not in resp.text and NUMBER[1:] not in resp.text and "5555550" not in resp.text
    assert bad[-1].json()["duty_officer_set"] is True
    assert all("5555550" not in rec.getMessage() for rec in caplog.records)


def test_dev_report_payload() -> None:
    hub = FakeHub()
    c, _ = client(hub)
    r = c.post("/dev/report", json={"lon": -118.1, "lat": 34.2, "kind": "embers", "observed_sim_ms": 1736302800000})
    assert r.status_code == 200
    ((name, (report, transcript)),) = hub.calls
    assert name == "submit_report_engine" and transcript == ""
    assert report["report_id"] == r.json()["report_id"]
    assert report["report_id"].startswith("rep_") and len(report["report_id"]) == 12
    assert {k: report[k] for k in ("channel", "trust", "provenance", "kind", "scenario_id", "branch_id")} == {
        "channel": "dashboard", "trust": 0.5, "provenance": "injected", "kind": "embers",
        "scenario_id": "fixture", "branch_id": "main",
    }
    assert (report["lon"], report["lat"], report["observed_sim_ms"]) == (-118.1, 34.2, 1736302800000)
    assert report["person_id"] is None and report["radius_m"] == 250.0


def test_dev_report_errors() -> None:
    c, _ = client(None)
    body = {"lon": -118.1, "lat": 34.2, "kind": "fire", "observed_sim_ms": 0}
    assert c.post("/dev/report", json=body).status_code == 503
    c, _ = client(FakeHub(reject=True))
    assert c.post("/dev/report", json=body).status_code == 502
    assert c.post("/dev/report", json=body | {"kind": "rain"}).status_code == 422
    assert c.post("/dev/report", json=body | {"observed_sim_ms": -5}).status_code == 422


def test_foreign_host_rejected() -> None:
    c, state = client(FakeHub())
    r = c.post("/dev/duty-officer", json={"number": NUMBER}, headers={"Host": "attacker.example:8787"})
    assert r.status_code == 400 and state.duty_officer_number is None
    assert c.get("/healthz", headers={"Host": "127.0.0.1:8787"}).status_code == 200


def test_seek_preset(monkeypatch: pytest.MonkeyPatch) -> None:
    hub = FakeHub()
    c, _ = client(hub, seek_presets={"moment2": 1736305200000})
    monkeypatch.delenv("OPERATOR_PIN", raising=False)
    assert c.post("/dev/seek-preset", json={"name": "moment2"}).status_code == 503
    monkeypatch.setenv("OPERATOR_PIN", "123456")
    assert c.post("/dev/seek-preset", json={"name": "nope"}).status_code == 404
    r = c.post("/dev/seek-preset", json={"name": "moment2"})
    assert r.status_code == 200 and "123456" not in r.text
    assert hub.calls == [("clock_seek", ("fixture", 1736305200000.0, "123456"))]


def test_healthz_and_cors() -> None:
    c, state = client(FakeHub())
    state.status.update(hub_ok=True, last_cycle_sim_ms=5, physics_ms=12.5, cache_coverage=0.5, backlog_cycles=3, publication_pending=2)
    h = c.get("/healthz", headers={"Origin": "http://localhost:5173"})
    assert h.json() == {
        "scenario_id": "fixture", "profile": "offline", "fixtures": True, "hub_ok": True,
        "cache_coverage": 0.5, "last_cycle_sim_ms": 5, "physics_ms": 12.5, "duty_officer_set": False,
        "backlog_cycles": 3, "publication_pending": 2,
    }
    assert h.headers["access-control-allow-origin"] == "http://localhost:5173"
    evil = c.get("/healthz", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in evil.headers
    assert "access-control-allow-origin" not in c.get("/healthz", headers={"Origin": "http://localhost.evil.com"}).headers
