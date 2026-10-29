"""Нагрузочный CLI: подаёт задания и считает фактический KPM и p95.

    python -m bench.bench --count 200 --concurrency 16 --api http://127.0.0.1:8080

Считает:
  * фактическую пропускную способность (заданий/мин и успехов/мин),
  * распределение статусов,
  * p50/p95/p99 по elapsed_ms (время потока на стороне воркера),
  * p50/p95 по времени от подачи до завершения (wall clock).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path
from typing import Any

import httpx


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


async def submit_one(
    client: httpx.AsyncClient, api: str, login: str, secret: str
) -> dict[str, Any]:
    started = time.perf_counter()
    resp = await client.post(
        f"{api}/api/v1/login", json={"login": login, "password": secret}
    )
    if resp.status_code not in (200, 202):
        return {"ok": False, "error": f"HTTP {resp.status_code}", "wall_ms": 0}
    payload = resp.json()
    job_id = payload["job_id"]
    return {"ok": True, "job_id": job_id, "accepted_at": started}


async def poll_one(
    client: httpx.AsyncClient, api: str, job_id: str, accepted_at: float, timeout: float
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = await client.get(f"{api}/api/v1/jobs/{job_id}")
        if resp.status_code != 200:
            return {"job_id": job_id, "status": "HTTP_ERROR", "wall_ms": 0}
        data = resp.json()
        if data.get("state") == "DONE":
            return {
                "job_id": job_id,
                "status": data.get("status"),
                "step": data.get("step"),
                "elapsed_ms": data.get("elapsed_ms") or 0,
                "wall_ms": int((time.perf_counter() - accepted_at) * 1000),
                "attempts": data.get("attempts") or 0,
            }
        await asyncio.sleep(0.25)
    return {"job_id": job_id, "status": "TIMEOUT", "wall_ms": int((time.perf_counter() - accepted_at) * 1000)}


async def run(args: argparse.Namespace) -> dict[str, Any]:
    limits = httpx.Limits(max_connections=args.concurrency * 2, max_keepalive_connections=args.concurrency)
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0), limits=limits) as client:
        health = await client.get(f"{args.api}/healthz")
        print(f"healthz -> {health.status_code} {health.text[:200]}")

        sem = asyncio.Semaphore(args.concurrency)
        started = time.perf_counter()
        results: list[dict[str, Any]] = []

        async def worker(idx: int) -> None:
            async with sem:
                login = f"{args.login_prefix}{idx:05d}"
                sub = await submit_one(client, args.api, login, args.secret)
                if not sub.get("ok"):
                    results.append({"status": "SUBMIT_FAILED", "wall_ms": 0, "job_id": None})
                    return
                res = await poll_one(client, args.api, sub["job_id"], sub["accepted_at"], args.timeout)
                results.append(res)

        await asyncio.gather(*(worker(i) for i in range(args.count)))
        finished = time.perf_counter()

    wall = finished - started
    statuses: dict[str, int] = {}
    for r in results:
        statuses[str(r.get("status"))] = statuses.get(str(r.get("status")), 0) + 1

    flow_ms = [float(r["elapsed_ms"]) for r in results if r.get("elapsed_ms")]
    wall_ms = [float(r["wall_ms"]) for r in results if r.get("wall_ms")]
    success = statuses.get("SUCCESS", 0)

    report = {
        "params": vars(args),
        "wall_seconds": round(wall, 2),
        "jobs": len(results),
        "jobs_per_min": round(len(results) / wall * 60, 1) if wall else 0,
        "success": success,
        "success_per_min": round(success / wall * 60, 1) if wall else 0,
        "statuses": statuses,
        "flow_ms": {
            "p50": round(_pct(flow_ms, 0.5), 1),
            "p95": round(_pct(flow_ms, 0.95), 1),
            "p99": round(_pct(flow_ms, 0.99), 1),
            "mean": round(statistics.fmean(flow_ms), 1) if flow_ms else 0,
        },
        "wall_ms": {
            "p50": round(_pct(wall_ms, 0.5), 1),
            "p95": round(_pct(wall_ms, 0.95), 1),
            "p99": round(_pct(wall_ms, 0.99), 1),
        },
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="нагрузочный контур SPK")
    parser.add_argument("--api", default="http://127.0.0.1:8080")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--login-prefix", default="bench_")
    parser.add_argument("--secret", default="bench_not_real")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--out", default="docs/bench-report.json")
    args = parser.parse_args()

    report = asyncio.run(run(args))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
