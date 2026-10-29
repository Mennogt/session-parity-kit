"""Маршруты API."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from api.schemas import CookieOut, HealthOut, JobResult, LoginAccepted, LoginRequest
from core.logging import get_logger, log
from core.statuses import JobState, Status
from obs import metrics as m
from obs.metrics import REGISTRY

router = APIRouter()
logr = get_logger("api")


def _container(request: Request) -> Any:
    return request.app.state.container


def _to_result(row: dict[str, Any]) -> JobResult:
    cookies_raw = row.get("cookies") or "[]"
    try:
        cookies = json.loads(cookies_raw)
    except json.JSONDecodeError:
        cookies = []
    return JobResult(
        job_id=str(row["job_id"]),
        login=str(row["login"]),
        state=str(row["state"]),
        status=Status(row["status"]) if row.get("status") else None,
        step=row.get("step"),
        detail=row.get("detail"),
        cookies=[CookieOut(**c) for c in cookies if isinstance(c, dict)],
        challenge_passed=bool(row.get("challenge_passed")),
        attempts=int(row.get("attempts") or 0),
        elapsed_ms=row.get("elapsed_ms"),
        retry_after=row.get("retry_after"),
    )


@router.post("/api/v1/login", response_model=None, status_code=202)
async def submit_login(body: LoginRequest, request: Request) -> Response | JobResult:
    c = _container(request)
    job_id, row = await asyncio.to_thread(
        c.store.create_job, body.login, body.password, body.job_id
    )
    log(logr, 20, "задание принято", job=job_id, state=row.get("state"))

    if row.get("state") != JobState.DONE:
        await c.queue.put(job_id)

    if body.wait_seconds > 0:
        deadline = asyncio.get_running_loop().time() + body.wait_seconds
        while asyncio.get_running_loop().time() < deadline:
            row = await asyncio.to_thread(c.store.get_job, job_id) or {}
            if row.get("state") == JobState.DONE:
                return Response(
                    content=_to_result(row).model_dump_json(),
                    status_code=200,
                    media_type="application/json",
                )
            await asyncio.sleep(0.2)

    row = await asyncio.to_thread(c.store.get_job, job_id) or row
    payload = LoginAccepted(
        job_id=job_id,
        state=str(row.get("state") or JobState.QUEUED),
        status=Status(row["status"]) if row.get("status") else None,
        detail=row.get("detail"),
    )
    return Response(
        content=payload.model_dump_json(),
        status_code=202,
        media_type="application/json",
    )


@router.get("/api/v1/jobs/{job_id}", response_model=JobResult)
async def get_job(job_id: str, request: Request) -> JobResult:
    c = _container(request)
    row = await asyncio.to_thread(c.store.get_job, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail={"status": "NOT_FOUND", "job_id": job_id})
    return _to_result(row)


@router.get("/healthz", response_model=HealthOut)
async def healthz(request: Request) -> HealthOut:
    c = _container(request)
    depth = int(await c.queue.depth())
    profiles = await asyncio.to_thread(c.store.profile_stats)
    stats = await asyncio.to_thread(c.store.stats)
    active = await asyncio.to_thread(c.store.active_workers, 30.0)
    m.queue_depth.set(float(depth))
    m.profile_pool_free.set(float(profiles["free"]))
    ok = profiles["free"] > 0 or depth == 0
    return HealthOut(
        ok=ok,
        queue_depth=depth,
        workers=active,
        profiles=profiles,
        stats=stats,
    )


@router.get("/metrics")
async def metrics_endpoint() -> Response:
    return Response(content=generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


@router.get("/api/v1/jobs", response_model=list[JobResult])
async def list_jobs(request: Request, limit: int = 20) -> list[JobResult]:
    c = _container(request)
    rows = await asyncio.to_thread(c.store.recent_jobs, max(1, min(limit, 200)))
    return [_to_result(r) for r in rows]
