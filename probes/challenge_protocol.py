"""Полный протокол проверки: что именно браузер отправляет и получает.

Нужно снять заголовки обмена `/v2/login` и последующего обращения к
`/v2/captcha`. Сайт делает второй запрос не с пустыми руками — он несёт
идентификатор проверки, выданный первым ответом. Без этого знания
HTTP-путь упирается в 404 и не понимает, что от него хотят.

    python -m probes.challenge_protocol --account 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from probes.m1_browser import STEALTH, UA

ART = Path("fixtures/browser")
WATCH = ("/v2/login", "/v2/captcha", "arkose", "px-cloud", "captcha.px")
INTERESTING = (
    "rblx-challenge", "x-csrf-token", "content-type", "origin", "referer",
    "sec-fetch", "user-agent", "cookie", "accept",
)


def pick(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if any(k.lower().startswith(p) for p in INTERESTING)}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, default=3)
    ap.add_argument("--wait", type=float, default=25.0)
    ap.add_argument("--out", default="fixtures/browser/challenge-protocol.json")
    args = ap.parse_args()

    accounts = json.loads(Path("var/accounts.json").read_text(encoding="utf-8"))
    account = accounts[args.account]

    from playwright.async_api import async_playwright

    events: list[dict] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, channel="chrome",
                                           args=["--disable-blink-features=AutomationControlled"])
        context = await browser.new_context(user_agent=UA, locale="ru-RU",
                                            timezone_id="Europe/Moscow",
                                            viewport={"width": 1366, "height": 900})
        await context.add_init_script(STEALTH)
        page = await context.new_page()

        def on_request(req) -> None:  # noqa: ANN001
            if not any(w in req.url for w in WATCH):
                return
            events.append({
                "kind": "request", "method": req.method, "url": req.url,
                "headers": pick(dict(req.headers)),
                "post_data": (req.post_data or "")[:600],
                "t": round(time.time(), 3),
            })

        def on_response(resp) -> None:  # noqa: ANN001
            if not any(w in resp.url for w in WATCH):
                return
            events.append({
                "kind": "response", "status": resp.status, "url": resp.url,
                "headers": pick(dict(resp.headers)),
                "t": round(time.time(), 3),
            })

        page.on("request", on_request)
        page.on("response", on_response)

        print(f"аккаунт {account['login']}: открываю форму и отправляю пару")
        await page.goto("https://www.roblox.com/login", wait_until="domcontentloaded", timeout=90_000)
        await page.wait_for_timeout(2500)
        await page.evaluate(
            "() => document.querySelectorAll('[class*=cookie-banner], .cookie-banner-bg')"
            ".forEach(e => e.remove())"
        )
        await page.fill("#login-username", account["login"])
        await page.fill("#login-password", account["password"])
        await page.click("#login-button")
        await page.wait_for_timeout(int(args.wait * 1000))
        await browser.close()

    Path(args.out).write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nсобытий: {len(events)} -> {args.out}")
    for e in events:
        if e["kind"] == "response":
            print(f"  ОТВЕТ  {e['status']} {e['url'][:90]}")
            ch = {k: v for k, v in e["headers"].items() if "challenge" in k.lower()}
            if ch:
                print(f"         заголовки проверки: {json.dumps(ch, ensure_ascii=False)[:400]}")
        else:
            print(f"  ЗАПРОС {e['method']} {e['url'][:90]}")
            ch = {k: v for k, v in e["headers"].items() if "challenge" in k.lower()}
            if ch:
                print(f"         проверка: {json.dumps(ch, ensure_ascii=False)[:400]}")
            if e.get("post_data"):
                print(f"         тело: {e['post_data'][:200]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
