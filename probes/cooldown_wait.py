"""Ждать выхода аккаунта из паузы и сразу оценить адреса.

Гейт держит аккаунт какое-то время после серии попыток. Пока он держит,
любая оценка адресов врёт: и доверенный, и помеченный адрес дадут гейт.
Скрипт периодически проверяет контроль напрямую и, как только аккаунт
отпустило, прогоняет отбор указанного пула.

    python -m probes.cooldown_wait --file C:\\Users\\404\\Desktop\\proxymob.txt --sample 10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from probes.pool_score import check_one, load

VAR = Path("var")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=r"C:\Users\404\Desktop\proxymob.txt")
    ap.add_argument("--sample", type=int, default=10)
    ap.add_argument("--account", type=int, default=0)
    ap.add_argument("--interval", type=float, default=600.0)
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--out", default="var/pool-score-mobile.json")
    args = ap.parse_args()

    accounts = json.loads((VAR / "accounts.json").read_text(encoding="utf-8"))
    login = accounts[args.account]["login"]
    proxies = load(Path(args.file))
    print(f"аккаунт {login}; прокси в файле: {len(proxies)}; "
          f"проверка каждые {args.interval:.0f} с", flush=True)

    for attempt in range(1, args.rounds + 1):
        control = await check_one(None, login, args.timeout)
        verdict = control.get("verdict", control.get("error"))
        print(f"{time.strftime('%H:%M:%S')} контроль {attempt}/{args.rounds}: {verdict}", flush=True)
        if verdict == "trusted":
            print("аккаунт отпустило — начинаю отбор", flush=True)
            break
        if attempt == args.rounds:
            print("за отведённое время аккаунт не отпустило", flush=True)
            return 2
        await asyncio.sleep(args.interval)

    sample = proxies[: args.sample]
    sem = asyncio.Semaphore(args.concurrency)
    results: list[dict] = []

    async def one(proxy: str) -> None:
        async with sem:
            row = await check_one(proxy, login, args.timeout)
            results.append(row)
            mark = {"trusted": "+", "flagged": "-"}.get(row.get("verdict"), "!")
            print(f"  [{mark}] {row['proxy']:<28} {row.get('verdict', row.get('error')):<8} "
                  f"{row.get('status', '-')} {row['ms']}мс", flush=True)

    await asyncio.gather(*(one(p) for p in sample))

    trusted = [r for r in results if r.get("verdict") == "trusted"]
    print(f"\nдоверенных: {len(trusted)} / {len(results)}", flush=True)
    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    if trusted:
        Path("var/trusted-pool.txt").write_text(
            "\n".join(r["proxy"] for r in trusted) + "\n", encoding="utf-8"
        )
        print("список сохранён: var/trusted-pool.txt", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
