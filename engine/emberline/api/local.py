"""Local API on :8787 (spec/03 §11, spec/00 §4 ASSET_BASE layout, WAVE2 §1.2).

T10's run.py mounts `create_app(state, hub)` with uvicorn on DEFAULT_HOST:DEFAULT_PORT.
The duty-officer number is held in memory only and never returned, logged or echoed.
"""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
from collections.abc import Awaitable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field

from emberline.contracts import Report
from emberline.sync.stdb_http import ReducerRejected, StdbHttp

log = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[3]
DEFAULT_HOST = "127.0.0.1"  # WAVE2 T12: loopback by default (03 §11 says 0.0.0.0; the brief wins)
DEFAULT_PORT = 8787
E164 = re.compile(r"\+[1-9]\d{1,14}")
SCN = re.compile(r"[A-Za-z0-9_-]+")
REPORT_RADIUS_M = 250.0  # 05 §4 tap fallback: max(250, 0.5 * 300) when no distance is known


@dataclass
class LocalState:
    scenario_id: str
    profile: str  # "online" | "offline"
    fixtures: bool  # serve fixtures/{static,cache} instead of data/ + cache/
    duty_officer_number: str | None = None  # set by POST /dev/duty-officer; memory only
    seek_presets: dict[str, int] = field(default_factory=dict)  # name -> sim_ms
    status: dict = field(default_factory=dict)  # engine writes: last_cycle_sim_ms, physics_ms, hub_ok, cache_coverage


class DutyOfficerIn(BaseModel):
    number: str


class ReportIn(BaseModel):
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)
    kind: Literal["fire", "smoke", "embers", "all_clear"]
    observed_sim_ms: int = Field(ge=0)  # future-dated reports are T10's §10.1 check: it knows sim_now


class SeekPresetIn(BaseModel):
    name: str


def mask(number: str) -> str:
    # ponytail: same format as T11's notify.cartesia.mask; switch to that import once it lands.
    return "•••• " + number[-4:]


def safe_file(root: Path, rel: str) -> Path:
    """Resolve `rel` under `root`; 400 on any `..` segment, 404 if outside root or missing."""
    if ".." in PurePosixPath(rel.replace("\\", "/")).parts:
        raise HTTPException(400, "path traversal")
    base = root.resolve()
    p = (base / rel).resolve()
    if not p.is_relative_to(base) or not p.is_file():
        raise HTTPException(404, "not found")
    return p


def _roots(state: LocalState) -> tuple[Path, Path]:
    """(cache root, static root); static root is joined with {scn}/ [web/]."""
    if state.fixtures:
        return REPO / "fixtures" / "cache", REPO / "fixtures" / "static"
    return REPO / "cache", REPO / "data" / "scenarios"


def _need_hub(hub: StdbHttp | None) -> StdbHttp:
    if hub is None:
        raise HTTPException(503, "no hub connection")
    return hub


async def _hub_call(coro: Awaitable[None]) -> None:
    try:
        await coro
    except ReducerRejected as e:
        raise HTTPException(502, f"hub rejected: {e.body}") from e
    except httpx.HTTPError as e:
        raise HTTPException(503, f"hub unreachable: {type(e).__name__}") from e


def create_app(state: LocalState, hub: StdbHttp | None) -> FastAPI:
    app = FastAPI(title="Emberline local API")
    app.add_middleware(GZipMiddleware)
    # DNS rebinding: after a rebind the attacker page is same-origin, so CORS alone does not stop it.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://localhost(:\d+)?",
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    cache_root, static_root = _roots(state)

    @app.exception_handler(RequestValidationError)
    async def _no_echo(_: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI echoes the request body in 422s; drop it so a bad duty-officer body never comes back.
        detail = [{"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return JSONResponse({"detail": detail}, status_code=422)

    @app.get("/cache/{path:path}")
    async def cache_file(path: str) -> FileResponse:
        p = safe_file(cache_root, path)
        # Deviation from the brief's `immutable`: cycle URLs are not content-addressed (a stale key or a
        # reused b{n} rewrites the same path), so revalidate. FileResponse's ETag makes that a cheap 304.
        return FileResponse(p, headers={"Cache-Control": "no-cache"})

    @app.get("/static/{scn}/{path:path}")
    async def static_file(scn: str, path: str) -> FileResponse:
        if not SCN.fullmatch(scn):
            raise HTTPException(400, "bad scenario id")
        base = static_root / scn if state.fixtures else static_root / scn / "web"
        return FileResponse(safe_file(base, path))

    @app.get("/healthz")
    async def healthz() -> dict:
        s = state.status
        return {
            "scenario_id": state.scenario_id,
            "profile": state.profile,
            "fixtures": state.fixtures,
            "hub_ok": bool(s.get("hub_ok", False)) and hub is not None,
            "cache_coverage": s.get("cache_coverage"),
            "last_cycle_sim_ms": s.get("last_cycle_sim_ms"),
            "physics_ms": s.get("physics_ms"),
            "backlog_cycles": s.get("backlog_cycles", 0),
            "publication_pending": s.get("publication_pending", 0),
            "duty_officer_set": state.duty_officer_number is not None,
        }

    @app.post("/dev/duty-officer")
    async def duty_officer(body: DutyOfficerIn) -> dict:
        number = body.number.strip()
        if not E164.fullmatch(number):
            raise HTTPException(400, "number must be E.164: + then up to 15 digits")
        state.duty_officer_number = number
        log.info("duty officer number set (%s)", mask(number))
        return {"ok": True, "masked": mask(number)}

    @app.post("/dev/report")
    async def report(body: ReportIn) -> dict:
        h = _need_hub(hub)
        r = Report(
            report_id=f"rep_{uuid.uuid4().hex[:8]}",
            scenario_id=state.scenario_id,
            branch_id="main",  # ponytail: §1.2 status has no active branch; add a key there if branch reports need it
            person_id=None,
            channel="dashboard",
            kind=body.kind,
            summary=f"Dashboard report: {body.kind}",
            lon=body.lon,
            lat=body.lat,
            radius_m=REPORT_RADIUS_M,
            trust=0.5,
            observed_sim_ms=body.observed_sim_ms,
            created_wall_ms=int(time.time() * 1000),  # the hub overwrites this with its own clock
        )
        await _hub_call(h.submit_report_engine(r.model_dump(mode="json"), ""))  # Report defaults provenance="injected"
        log.info("dashboard report %s submitted", r.report_id)
        return {"ok": True, "report_id": r.report_id}

    @app.post("/dev/seek-preset")
    async def seek_preset(body: SeekPresetIn) -> dict:
        if body.name not in state.seek_presets:
            raise HTTPException(404, f"unknown preset; known: {sorted(state.seek_presets)}")
        h = _need_hub(hub)
        pin = os.environ.get("OPERATOR_PIN", "")
        if not pin:
            raise HTTPException(503, "OPERATOR_PIN is not set")
        sim_ms = state.seek_presets[body.name]
        await _hub_call(h.call("clock_seek", state.scenario_id, float(sim_ms), pin))
        return {"ok": True, "name": body.name, "sim_ms": sim_ms}

    return app
