"""Нужен ли токен PerimeterX для свежего аккаунта.

Опыт: берём cookie-банку со страницы входа (там JS-скрипт защиты выставляет
`_px3`, `_pxvid`, `pxcts`), затем повторяем обмен пары обычным HTTP-клиентом
с этого же адреса. Цель — понять, снимает ли валидный PX-токен гейт у
аккаунта, у которого нет истории входов.

    python -m probes.px_bootstrap --account 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
ART = Path("fixtures/browser")

PROBE_HEADERS = {
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


def verdict(status: int, body: str) -> str:
    low = body.lower()
    if "token validation failed" in low:
        return "csrf-only"
    if "challenge is required" in low:
        return "GATE"
    if "incorrect username or password" in low:
        return "NO GATE"
    if status == 200:
        return "SUCCESS"
    return f"other:{status}"


async def login(login_name: str, password: str, cookies: dict[str, str],
                proxy: str | None) -> dict:
    body = {"ctype": "Username", "cvalue": login_name, "password": password}
    async with httpx.AsyncClient(
        proxy=proxy, timeout=httpx.Timeout(connect=20, read=40, write=40, pool=20),
        follow_redirects=False, cookies=cookies,
    ) as client:
        r = await client.post("https://auth.roblox.com/v2/login",
                              headers=PROBE_HEADERS, json=body)
        token = r.headers.get("x-csrf-token")
        if token:
            headers = dict(PROBE_HEADERS)
            headers["X-CSRF-TOKEN"] = token
            r = await client.post("https://auth.roblox.com/v2/login",
                                  headers=headers, json=body)
        return {
            "status": r.status_code,
            "verdict": verdict(r.status_code, r.text),
            "body": r.text[:200],
            "session": any(c.name == ".ROBLOSECURITY" for c in client.cookies.jar),
            "jar": {c.name: c.value for c in client.cookies.jar},
        }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, default=3)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--wait", type=float, default=9.0)
    args = ap.parse_args()

    ART.mkdir(parents=True, exist_ok=True)
    accounts = json.loads(Path("var/accounts.json").read_text(encoding="utf-8"))
    account = accounts[args.account]
    proxy = None
    if args.port:
        from probes.proxy_check import proxy_url

        proxy = proxy_url(args.port)

    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True, channel="chrome",
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=UA, locale="ru-RU", timezone_id="Europe/Moscow",
            viewport={"width": 1366, "height": 900},
            proxy={"server": f"http://{proxy.split('@')[-1]}",
                   "username": proxy.split('//')[1].split(':')[0],
                   "password": proxy.split(':')[2].split('@')[0]} if args.port else None,
        )
        page = await context.new_page()
        print("открываю страницу входа, жду выдачу cookie защитой...")
        await page.goto("https://www.roblox.com/login", wait_until="domcontentloaded", timeout=90_000)
        deadline = time.monotonic() + args.wait
        jar: dict[str, str] = {}
        while time.monotonic() < deadline:
            await page.wait_for_timeout(1000)
            jar = {c["name"]: c["value"] for c in await context.cookies()}
            if "_px3" in jar:
                break
        await browser.close()

    print("cookie со страницы:", sorted(jar))
    print("  _px3:", "ЕСТЬ, длина " + str(len(jar.get("_px3", ""))) if "_px3" in jar else "нет")
    Path("var/px-bootstrap.json").write_text(json.dumps(jar, ensure_ascii=False, indent=2),
                                             encoding="utf-8")

    if not jar:
        print("cookie не получены — опыт невозможен")
        return 2

    with_px = await login(account["login"], account["password"], jar, proxy)
    print("\nвход с этой банкой:", with_px["verdict"], with_px["status"],
          "| сессия:", with_px["session"])
    print("  тело:", with_px["body"][:140])

    only_rbx = {k: v for k, v in jar.items() if k in ("rbx-ip2", "GuestData", "RBXEventTrackerV2")}
    without_px = await login(account["login"], account["password"], only_rbx, proxy)
    print("вход только с rbx-ip2/гостевыми:", without_px["verdict"], without_px["status"])

    print("\n=== вывод ===")
    if with_px["verdict"] != "GATE" and without_px["verdict"] == "GATE":
        print("PX-токен снимает гейт; rbx-ip2 сам по себе — нет")
    elif with_px["verdict"] == "GATE":
        print("PX-токен не снимает гейт: аккаунт требует проверки независимо от cookie")
    else:
        print("оба варианта без гейта: решает не токен, а что-то ещё")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
