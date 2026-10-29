"""Какой выходной IP даёт каждый порт прокси.

Нужно, чтобы понять: пул портов — это пул разных адресов (тогда лимит
платформы делится на всех) или один адрес с разными туннелями (тогда нет).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import httpx

from probes.proxy_check import proxy_url

TRACE = "https://www.cloudflare.com/cdn-cgi/trace"


async def ip_of(port: int, timeout: float) -> dict:
    try:
        async with httpx.AsyncClient(
            proxy=proxy_url(port),
            timeout=httpx.Timeout(connect=timeout, read=timeout, write=timeout, pool=timeout),
        ) as client:
            resp = await client.get(TRACE)
        data = {}
        for line in resp.text.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                data[k] = v
        return {"port": port, "ip": data.get("ip"), "loc": data.get("loc"), "ok": True}
    except Exception as exc:
        return {"port": port, "ok": False, "error": f"{type(exc).__name__}: {str(exc)[:100]}"}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ports", default="10000,10001,10002,10003,10004,10005")
    ap.add_argument("--timeout", type=float, default=25.0)
    ap.add_argument("--out", default="var/proxy-ips.json")
    args = ap.parse_args()

    ports = [int(p) for p in args.ports.split(",") if p.strip()]
    results = await asyncio.gather(*(ip_of(p, args.timeout) for p in ports))
    Path(args.out).write_text(json.dumps(list(results), ensure_ascii=False, indent=2), encoding="utf-8")
    for r in results:
        if r["ok"]:
            print(f"  {r['port']} -> {r['ip']} ({r['loc']})")
        else:
            print(f"  {r['port']} -> ошибка: {r['error']}")
    uniq = {r.get("ip") for r in results if r.get("ok")}
    print(f"уникальных IP: {len(uniq)} из {len(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
