"""Как платформа выдаёт проверку: разбор эндпоинта captcha.

Сайт после отказа `Challenge is required` обращается к
`apis.roblox.com/v2/captcha` и уже оттуда получает параметры проверки.
Значит ответ обмена может не содержать `captchaId` не потому, что его нет,
а потому, что клиент должен взять его здесь.

Проверяем формы запроса и ответа, включая заголовки вида
`rblx-challenge-*`, которыми платформа обменивается с клиентом.

    python -m probes.captcha_endpoint --account 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import httpx

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://www.roblox.com",
    "Referer": "https://www.roblox.com/login",
}
VARIANTS = [
    ("GET  /v2/captcha", "GET", "https://apis.roblox.com/v2/captcha", None),
    ("GET  ?urlLocale", "GET", "https://apis.roblox.com/v2/captcha?urlLocale=en_us", None),
    ("GET  ?actionType", "GET",
     "https://apis.roblox.com/v2/captcha?actionType=ACTION_TYPE_WEB_LOGIN", None),
    ("POST /v2/captcha", "POST", "https://apis.roblox.com/v2/captcha",
     {"actionType": "ACTION_TYPE_WEB_LOGIN"}),
    ("GET  metadata", "GET", "https://apis.roblox.com/captcha/v1/metadata", None),
]


async def prime(client: httpx.AsyncClient) -> None:
    """Получить гостевые cookie и токен запроса, как это делает страница."""
    await client.get("https://www.roblox.com/login", headers={**HEADERS, "Accept": "text/html"})
    r = await client.post("https://auth.roblox.com/v2/login", headers=HEADERS,
                          json={"ctype": "Username", "cvalue": "probe_nonexistent_zz", "password": "x"})
    token = r.headers.get("x-csrf-token")
    if token:
        client.headers["X-CSRF-TOKEN"] = token


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, default=3)
    ap.add_argument("--out", default="var/captcha-endpoint.json")
    args = ap.parse_args()

    results: list[dict] = []
    async with httpx.AsyncClient(timeout=40, follow_redirects=False, headers=dict(HEADERS)) as client:
        await prime(client)
        print("cookie после прогрева:", sorted(c.name for c in client.cookies.jar))
        for label, method, url, body in VARIANTS:
            try:
                if method == "GET":
                    r = await client.get(url, headers=dict(client.headers))
                else:
                    r = await client.post(url, headers=dict(client.headers), json=body)
            except Exception as exc:
                results.append({"label": label, "error": f"{type(exc).__name__}: {exc}"})
                print(f"  {label:<20} ошибка {type(exc).__name__}")
                continue
            row = {
                "label": label,
                "status": r.status_code,
                "body": r.text[:400],
                "challenge_headers": {
                    k: v for k, v in r.headers.items() if k.startswith("rblx-challenge")
                },
                "headers": {k: v for k, v in r.headers.items()
                            if k in ("content-type", "x-csrf-token")},
            }
            results.append(row)
            print(f"  {label:<20} {r.status_code} {r.text[:110]!r}")
            if row["challenge_headers"]:
                print(f"      заголовки проверки: {row['challenge_headers']}")

    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== что вернулось ===")
    for r in results:
        if r.get("status") == 200 and r.get("body"):
            print(f"  {r['label']}: {r['body'][:200]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
