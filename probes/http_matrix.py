"""Матрица: один и тот же аккаунт, разные HTTP-клиенты, разные IP.

Цель — отделить причину проверки. Если проверка зависит от аккаунта, она
появится у всех клиентов. Если от отпечатка клиента — у httpx появится, у
клиента с отпечатком Chrome исчезнет.

    python -m probes.http_matrix --accounts 0,3,4,5 --ports 10060,10061

Стоимость: 2 запроса на попытку, около 3 КБ. Браузер не используется.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx

from flow.detect import classify_exchange
from probes.proxy_check import proxy_url

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
ACCOUNTS = Path("var/accounts.json")


def headers(token: str | None = None) -> dict[str, str]:
    h = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://www.roblox.com",
        "Referer": "https://www.roblox.com/login",
        "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
    }
    if token:
        h["X-CSRF-TOKEN"] = token
    return h


async def via_httpx(account: dict, port: int) -> dict:
    body = {"ctype": "Username", "cvalue": account["login"], "password": account["password"]}
    async with httpx.AsyncClient(
        proxy=proxy_url(port),
        timeout=httpx.Timeout(connect=15, read=25, write=25, pool=15),
        follow_redirects=False,
    ) as client:
        r1 = await client.post("https://auth.roblox.com/v2/login", headers=headers(),
                               content=json.dumps(body))
        token = r1.headers.get("x-csrf-token")
        r2 = await client.post("https://auth.roblox.com/v2/login", headers=headers(token),
                               content=json.dumps(body))
        cls = classify_exchange(r2.status_code, r2.headers, r2.content)
        return {"client": "httpx", "status": r2.status_code, "cls": str(cls.status),
                "detail": cls.detail[:160], "body": r2.text[:200],
                "cookies": sorted({c.name for c in client.cookies.jar})}


async def via_curl_cffi(account: dict, port: int, impersonate: str) -> dict:
    from curl_cffi.requests import AsyncSession

    body = {"ctype": "Username", "cvalue": account["login"], "password": account["password"]}
    proxy = proxy_url(port)
    attempts = [
        {"impersonate": impersonate, "proxy": proxy},
        {"impersonate": impersonate, "proxies": {"http": proxy, "https": proxy}},
    ]
    last_error = ""
    for kwargs in attempts:
        try:
            async with AsyncSession(timeout=30, **kwargs) as client:  # type: ignore[arg-type]
                r1 = await client.post(
                    "https://auth.roblox.com/v2/login",
                    headers=headers(), json=body, allow_redirects=False,
                )
                token = r1.headers.get("x-csrf-token")
                r2 = await client.post(
                    "https://auth.roblox.com/v2/login",
                    headers=headers(token), json=body, allow_redirects=False,
                )
                cls = classify_exchange(r2.status_code, r2.headers, r2.text)
                return {"client": f"curl_cffi:{impersonate}", "status": r2.status_code,
                        "cls": str(cls.status), "detail": cls.detail[:160],
                        "body": r2.text[:200],
                        "cookies": sorted(set(client.cookies.keys())) if hasattr(client.cookies, "keys") else []}
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {str(exc)[:140]}"
    return {"client": f"curl_cffi:{impersonate}", "error": last_error}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--accounts", default="0,3,4,5")
    ap.add_argument("--ports", default="10060,10061")
    ap.add_argument("--impersonate", default="chrome")
    ap.add_argument("--clients", default="httpx,curl_cffi")
    ap.add_argument("--force-password", default=None,
                    help="подменить пароль (контроль: неверный пароль не должен давать проверку)")
    ap.add_argument("--out", default="var/http-matrix.json")
    args = ap.parse_args()

    accounts_raw = json.loads(ACCOUNTS.read_text(encoding="utf-8"))
    idxs = [int(x) for x in args.accounts.split(",") if x.strip()]
    ports = [int(p) for p in args.ports.split(",") if p.strip()]
    clients = [c.strip() for c in args.clients.split(",") if c.strip()]

    rows: list[dict] = []
    port_cursor = 0
    for idx in idxs:
        account = dict(accounts_raw[idx])
        if args.force_password is not None:
            account["password"] = args.force_password
        # Один аккаунт на много портов: цикл по клиентам разворачивается
        # в повторные попытки, чтобы набор портов не простаивал.
        attempts = len(clients) if len(idxs) > 1 else len(ports)
        for attempt in range(attempts):
            client_name = clients[attempt % len(clients)] if len(idxs) > 1 else clients[0]
            port = ports[port_cursor % len(ports)]
            port_cursor += 1
            started = time.perf_counter()
            if client_name == "httpx":
                row = await via_httpx(account, port)
            else:
                row = await via_curl_cffi(account, port, args.impersonate)
            row.update({
                "account": account["login"], "port": port,
                "ms": int((time.perf_counter() - started) * 1000),
            })
            rows.append(row)
            label = row.get("cls") or row.get("error") or "-"
            print(f"{row['account']:<18} {row['client']:<16} порт {port}  "
                  f"-> {row.get('status', '-')} {label}")
            if row.get("detail"):
                print(f"    {row['detail']}")
            await asyncio.sleep(0.6)

    Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    def tally(key: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in rows:
            v = str(r.get(key, "?"))
            out[v] = out.get(v, 0) + 1
        return out

    print("\n=== сводка по клиентам ===")
    for r in rows:
        print(f"  {r['client']:<16} {r['account']:<18} {r.get('cls', r.get('error'))}")
    print("итого:", tally("cls"))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
