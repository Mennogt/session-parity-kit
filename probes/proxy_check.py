"""Генерация списка прокси из одного шаблона и проверка их живости.

Провайдер выдал один и тот же логин/пароль на диапазон портов, поэтому
список строится из шаблона, а не хранится как 100 одинаковых строк.

    python -m probes.proxy_check --first 10000 --last 10099 --limit 20

Проверка идёт через сам прокси к нейтральному эндпоинту платформы: важен
не «интернет вообще», а доступность именно нужного хоста.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx

USER = "448d9c1a971f98e67c06__cr.lv,us,no,fr"
PASSWORD = "2e45d19f6c37c13d"
HOST = "pw.retryproxy.net"
CHECK_URL = "https://apis.roblox.com/captcha/v1/metadata"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def proxy_url(port: int) -> str:
    return f"http://{USER}:{PASSWORD}@{HOST}:{port}"


def generate(first: int, last: int, path: Path) -> list[int]:
    ports = list(range(first, last + 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(f"{HOST}:{p}" for p in ports) + "\n", encoding="utf-8"
    )
    return ports


async def check(client_ports: list[int], timeout: float) -> list[dict]:
    results: list[dict] = []
    sem = asyncio.Semaphore(20)

    async def one(port: int) -> None:
        async with sem:
            started = time.perf_counter()
            try:
                async with httpx.AsyncClient(
                    proxy=proxy_url(port),
                    timeout=httpx.Timeout(connect=timeout, read=timeout, write=timeout, pool=timeout),
                    follow_redirects=False,
                ) as client:
                    resp = await client.get(CHECK_URL, headers={"User-Agent": UA})
                    ip = "?"
                    try:
                        ip = resp.headers.get("cf-ray", "")[-3:] or "?"
                    except Exception:
                        pass
                results.append(
                    {
                        "port": port,
                        "ok": resp.status_code < 500,
                        "status": resp.status_code,
                        "ms": int((time.perf_counter() - started) * 1000),
                        "cf_ray_tail": ip,
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "port": port,
                        "ok": False,
                        "error": f"{type(exc).__name__}: {str(exc)[:120]}",
                        "ms": int((time.perf_counter() - started) * 1000),
                    }
                )

    await asyncio.gather(*(one(p) for p in client_ports))
    return sorted(results, key=lambda r: r["port"])


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--first", type=int, default=10000)
    ap.add_argument("--last", type=int, default=10099)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--timeout", type=float, default=25.0)
    ap.add_argument("--out", default="var/proxy-check.json")
    args = ap.parse_args()

    ports = generate(args.first, args.last, Path("var/proxies.txt"))
    print(f"портов в шаблоне: {len(ports)}")
    sample = ports[: args.limit]
    print(f"проверяю первых: {len(sample)}")

    results = await check(sample, args.timeout)
    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    alive = [r for r in results if r.get("ok")]
    print(f"живых: {len(alive)} из {len(results)}")
    for r in results[:10]:
        print(f"  {r['port']}: {'ok' if r.get('ok') else 'fail'} "
              f"{r.get('status', r.get('error', ''))} {r['ms']}ms")
    if alive:
        print("живые порты:", ", ".join(str(r["port"]) for r in alive[:20]))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
