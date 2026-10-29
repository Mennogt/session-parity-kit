"""Повторные попытки входа: гейт абсолютный или вероятностный?

Если гейт срабатывает не на каждой попытке, то перебор попыток даёт сессии
без браузера и без решателя — просто дольше. Проверяем на аккаунте без
истории входов.

    python -m probes.gate_retry --account 3 --attempts 15 --delay 20
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
    ap.add_argument("--account", type=int, default=3)
    ap.add_argument("--attempts", type=int, default=15)
    ap.add_argument("--delay", type=float, default=20.0)
    ap.add_argument("--prime", action="store_true", default=True)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--out", default="var/gate-retry.json")
    args = ap.parse_args()

    settings = get_settings()
    settings.prime_session = args.prime
    setup_logging("ERROR", as_json=False)

    accounts = json.loads(Path("var/accounts.json").read_text(encoding="utf-8"))
    account = accounts[args.account]
    proxy = None
    if args.port:
        from probes.proxy_check import proxy_url

        proxy = proxy_url(args.port)

    flow = FlowClient(settings)
    rows: list[dict] = []
    print(f"аккаунт {account['login']}, попыток {args.attempts}, пауза {args.delay}с, "
          f"выход: {'прямой IP' if proxy is None else args.port}")

    started = time.perf_counter()
    for i in range(1, args.attempts + 1):
        profile = Profile(id=f"gr-{i:03d}", proxy_url=proxy, user_agent=UA)
        t0 = time.perf_counter()
        outcome, _ = await flow.execute(
            profile, account["login"], account["password"], f"gr-{i:03d}",
            lambda a, b: (_ for _ in ()).throw(RuntimeError("no challenge")),
        )
        ms = int((time.perf_counter() - t0) * 1000)
        rows.append({"n": i, "status": str(outcome.status), "ms": ms,
                     "at": time.strftime("%H:%M:%S")})
        print(f"  {i:>2}/{args.attempts} {time.strftime('%H:%M:%S')} "
              f"{outcome.status:<18} {ms} мс "
              f"{'<-- СЕССИЯ' if outcome.status == 'SUCCESS' else ''}", flush=True)
        Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        if i < args.attempts:
            await asyncio.sleep(args.delay)

    ok = sum(1 for r in rows if r["status"] == "SUCCESS")
    total = time.perf_counter() - started
    print("\n=== итог ===")
    print(f"  успехов: {ok} из {len(rows)} ({ok / len(rows) * 100:.0f}%)")
    print(f"  время: {total:.0f} с")
    if ok:
        print(f"  одна сессия в среднем за {total / ok:.0f} с и {len(rows) / ok:.1f} попыток")
        print(f"  запросов на сессию примерно {len(rows) * 3 / ok:.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
