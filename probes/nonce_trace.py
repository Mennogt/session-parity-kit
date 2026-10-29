"""Откуда берётся serverNonce для подписи входа.

Браузер отправляет в теле входа `secureAuthenticationIntent` с полями
`clientPublicKey`, `clientEpochTimestamp`, `serverNonce`, `saiSignature`.
Нужно понять, кто выдаёт `serverNonce`: скрипт страницы его получает
каким-то ответом. Скрипт ловит все ответы, вытаскивает nonce из тела
входа и ищет его же в остальных ответах.

    python -m probes.nonce_trace --account 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path

from probes.m1_browser import STEALTH, UA

ART = Path("fixtures/browser")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, default=3)
    ap.add_argument("--wait", type=float, default=20.0)
    ap.add_argument("--out", default="fixtures/browser/nonce-trace.json")
    args = ap.parse_args()

    accounts = json.loads(Path("var/accounts.json").read_text(encoding="utf-8"))
    account = accounts[args.account]

    from playwright.async_api import async_playwright

    login_body = ""
    captured: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, channel="chrome",
                                          args=["--disable-blink-features=AutomationControlled"])
        context = await browser.new_context(user_agent=UA, locale="ru-RU",
                                            timezone_id="Europe/Moscow",
                                            viewport={"width": 1366, "height": 900})
        await context.add_init_script(STEALTH)
        page = await context.new_page()

        async def on_response(resp) -> None:  # noqa: ANN001
            ctype = (resp.headers.get("content-type") or "")
            if not any(t in ctype for t in ("json", "text/plain", "text/html")):
                return
            try:
                text = await resp.text()
            except Exception:
                return
            if len(text) > 200_000:
                text = text[:200_000]
            captured.append({"url": resp.url, "status": resp.status, "body": text})

        page.on("response", lambda r: asyncio.create_task(on_response(r)))

        print(f"аккаунт {account['login']}: открываю форму")
        await page.goto("https://www.roblox.com/login", wait_until="domcontentloaded", timeout=90_000)
        await page.wait_for_timeout(2500)
        await page.evaluate(
            "() => document.querySelectorAll('[class*=cookie-banner], .cookie-banner-bg')"
            ".forEach(e => e.remove())"
        )

        # Перехватываем тело входа целиком.
        bodies: list[str] = []

        def on_request(req) -> None:  # noqa: ANN001
            if "/v2/login" in req.url and req.method == "POST" and req.post_data:
                bodies.append(req.post_data)

        page.on("request", on_request)

        await page.fill("#login-username", account["login"])
        await page.fill("#login-password", account["password"])
        await page.click("#login-button")
        await page.wait_for_timeout(int(args.wait * 1000))
        await browser.close()

        login_body = bodies[0] if bodies else ""
        print(f"тело входа получено: {len(login_body)} символов")

        nonce = ""
        if login_body:
            try:
                parsed = json.loads(login_body)
                nonce = (parsed.get("secureAuthenticationIntent") or {}).get("serverNonce", "")
            except json.JSONDecodeError:
                m = re.search(r'"serverNonce"\s*:\s*"([^"]+)"', login_body)
                nonce = m.group(1) if m else ""
        print(f"serverNonce: {nonce!r}")

        hits = []
        if nonce:
            for item in captured:
                if nonce in item["body"]:
                    hits.append({"url": item["url"], "status": item["status"],
                                 "snippet": item["body"][:500]})
        print(f"ответов, содержащих nonce: {len(hits)}")
        for h in hits:
            print(f"  {h['status']} {h['url'][:110]}")
            print(f"     {h['snippet'][:220]}")

        Path(args.out).write_text(
            json.dumps({"login_body": login_body, "nonce": nonce, "hits": hits},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nсохранено: {args.out}")
        if not hits and nonce:
            print("nonce не найден в ответах — вероятно, он вычислен на клиенте")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
