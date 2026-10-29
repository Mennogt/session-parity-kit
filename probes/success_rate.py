"""Реальная пропускная способность на прогретом аккаунте.

Повторяет вход N раз подряд с прогревом страницы и считает исходы и время.
Нужен, чтобы получить цифру «SUCCESS в минуту» на настоящих сессиях, а не
на отказах.

    python -m probes.success_rate --account 0 --count 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

from core.config import get_settings
from core.logging import setup_logging
from flow.client import FlowClient
from profiles.model import Profile
from probes.m1_browser import UA


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, default=0)
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--pause", type=float, default=1.5)
    ap.add_argument("--out", default="var/success-rate.json")
    args = ap.parse_args()

    settings = get_settings()
    settings.prime_session = True
    setup_logging("WARNING", as_json=False)

    accounts = json.loads(Path("var/accounts.json").read_text(encoding="utf-8"))
    account = accounts[args.account]

    proxy = None
    if args.port:
        from probes.proxy_check import proxy_url

        proxy = proxy_url(args.port)

    flow = FlowClient(settings)
    outcomes: list[dict] = []
    started = time.perf_counter()

    for i in range(1, args.count + 1):
        profile = Profile(id=f"sr-{i:03d}", proxy_url=proxy, user_agent=UA)
        t0 = time.perf_counter()
        outcome, _ = await flow.execute(
            profile, account["login"], account["password"], f"sr-{i:03d}",
            lambda prompt, prof: (_ for _ in ()).throw(
                RuntimeError("проверка не поддерживается в этом замере")
            ),
        )
        elapsed = int((time.perf_counter() - t0) * 1000)
        outcomes.append({"n": i, "status": str(outcome.status), "ms": elapsed,
                         "detail": outcome.detail[:120]})
        print(f"  {i:>3}/{args.count} {outcome.status:<18} {elapsed:>5} мс")
        if i < args.count:
            await asyncio.sleep(args.pause)

    total = time.perf_counter() - started
    ok = sum(1 for o in outcomes if o["status"] == "SUCCESS")
    times = [o["ms"] for o in outcomes if o["status"] == "SUCCESS"] or [o["ms"] for o in outcomes]

    def pct(values: list[float], q: float) -> float:
        ordered = sorted(values)
        if len(ordered) == 1:
            return float(ordered[0])
        pos = (len(ordered) - 1) * q
        lo = int(pos)
        hi = min(lo + 1, len(ordered) - 1)
        return ordered[lo] * (1 - (pos - lo)) + ordered[hi] * (pos - lo)

    report = {
        "account": account["login"],
        "count": args.count,
        "success": ok,
        "success_rate": round(ok / args.count * 100, 1),
        "wall_seconds": round(total, 1),
        "success_per_min": round(ok / total * 60, 1),
        "p50_ms": round(pct(times, 0.5)),
        "p95_ms": round(pct(times, 0.95)),
        "outcomes": outcomes,
    }
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== итог ===")
    for k in ("success", "success_rate", "success_per_min", "p50_ms", "p95_ms"):
        print(f"  {k}: {report[k]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
