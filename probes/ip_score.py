"""Оценка доверия прокси по реакции платформы.

Идея: у существующего аккаунта платформа сначала решает, доверять ли запросу,
и только потом проверяет пароль. Поэтому запрос с **заведомо неверным**
паролем даёт два разных исхода:

  BAD_CREDENTIALS       — адрес прошёл риск-проверку, гейта нет
  CHALLENGE_FAILED      — адрес помечен, требуется проверка

Пароль специально неверный: успешного входа не происходит, аккаунт не
расходуется, стоимость — 2 запроса (~3 КБ) на адрес.

    python -m probes.ip_score --ports 10100-10119 --account 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx

from flow.detect import classify_exchange
from probes.http_matrix import headers
from probes.proxy_check import proxy_url

PROBE_PASSWORD = "ip_score_probe_not_a_real_password"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def parse_ports(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.extend(range(int(lo), int(hi) + 1))
        else:
            out.append(int(part))
    return out


async def score(port: int, login: str, timeout: float) -> dict:
    body = {"ctype": "Username", "cvalue": login, "password": PROBE_PASSWORD}
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            proxy=proxy_url(port),
            timeout=httpx.Timeout(connect=timeout, read=timeout, write=timeout, pool=timeout),
            follow_redirects=False,
        ) as client:
            r1 = await client.post("https://auth.roblox.com/v2/login",
                                   headers=headers(), content=json.dumps(body))
            token = r1.headers.get("x-csrf-token")
            r2 = await client.post("https://auth.roblox.com/v2/login",
                                   headers=headers(token), content=json.dumps(body))
        cls = classify_exchange(r2.status_code, r2.headers, r2.content)
        verdict = "flagged"
        if cls.status is not None and "BAD_CREDENTIALS" in str(cls.status):
            verdict = "trusted"
        elif cls.status is None:
            verdict = "unknown"
        return {"port": port, "verdict": verdict, "status": r2.status_code,
                "cls": str(cls.status), "ms": int((time.perf_counter() - started) * 1000)}
    except Exception as exc:
        return {"port": port, "verdict": "error", "error": f"{type(exc).__name__}",
                "ms": int((time.perf_counter() - started) * 1000)}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ports", default="10100-10119")
    ap.add_argument("--account", type=int, default=3)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--out", default="var/ip-score.json")
    args = ap.parse_args()

    accounts = json.loads(Path("var/accounts.json").read_text(encoding="utf-8"))
    login = accounts[args.account]["login"]
    ports = parse_ports(args.ports)
    print(f"проверяю {len(ports)} адресов, логин для пробы: {login}")

    sem = asyncio.Semaphore(args.concurrency)
    results: list[dict] = []

    async def one(port: int) -> None:
        async with sem:
            row = await score(port, login, args.timeout)
            results.append(row)
            mark = {"trusted": "+", "flagged": "-", "unknown": "?", "error": "!"}[row["verdict"]]
            print(f"  [{mark}] {port}  {row['verdict']:<8} {row.get('cls', row.get('error', ''))}")

    await asyncio.gather(*(one(p) for p in ports))
    results.sort(key=lambda r: r["port"])

    tally: dict[str, int] = {}
    for r in results:
        tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
    trusted = [r["port"] for r in results if r["verdict"] == "trusted"]

    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== итог ===")
    print(" ", tally)
    print(f"  доверенных: {len(trusted)} из {len(results)} "
          f"({len(trusted) / max(1, len(results)) * 100:.0f}%)")
    if trusted:
        print("  порты:", ", ".join(str(p) for p in trusted[:30]))
        Path("var/trusted-ports.txt").write_text(
            "\n".join(str(p) for p in trusted) + "\n", encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
