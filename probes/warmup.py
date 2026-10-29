"""Прогрев аккаунта: видимое окно, удержание проходит человек, дальше — замер.

Скрипт делает всё, кроме самого удержания: открывает браузер через прокси,
вводит пару, показывает проверку. Человек держит кнопку. Как только
появляется сессионная cookie, скрипт:

  1. сохраняет cookie-банку профиля в var/session-<login>.json;
  2. проверяет сессию контрольным запросом;
  3. повторяет вход **обычным HTTP-клиентом** с этой банкой и тем же IP —
     это и есть ответ на вопрос, снимается ли гейт после прогрева.

    python -m probes.warmup --account 0 --port 10100
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx

from flow.detect import classify_exchange
from probes.m1_browser import STEALTH, UA, load_account, proxy_conf
from probes.proxy_check import proxy_url

ART = Path("fixtures/browser")
VAR = Path("var")


def headers(token: str | None = None) -> dict[str, str]:
    h = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://www.roblox.com",
        "Referer": "https://www.roblox.com/login",
    }
    if token:
        h["X-CSRF-TOKEN"] = token
    return h


async def http_replay(cookies: dict[str, str], port: int, account: dict) -> dict:
    """Повторный вход обычным HTTP-клиентом с cookie-банкой браузера."""
    body = {"ctype": "Username", "cvalue": account["login"], "password": account["password"]}
    try:
        async with httpx.AsyncClient(
            proxy=proxy_url(port),
            cookies=cookies,
            timeout=httpx.Timeout(connect=20, read=30, write=30, pool=20),
            follow_redirects=False,
        ) as client:
            r1 = await client.post("https://auth.roblox.com/v2/login",
                                   headers=headers(), content=json.dumps(body))
            token = r1.headers.get("x-csrf-token")
            r2 = await client.post("https://auth.roblox.com/v2/login",
                                   headers=headers(token), content=json.dumps(body))
            cls = classify_exchange(r2.status_code, r2.headers, r2.content)
            return {
                "status": r2.status_code,
                "cls": str(cls),
                "detail": cls.detail[:200],
                "body": r2.text[:300],
                "session_cookie": any(
                    c.name == ".ROBLOSECURITY" for c in client.cookies.jar
                ),
            }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, default=0)
    ap.add_argument("--port", type=int, default=10100)
    ap.add_argument("--wait", type=float, default=900.0, help="сколько ждать человека, секунд")
    args = ap.parse_args()

    ART.mkdir(parents=True, exist_ok=True)
    VAR.mkdir(parents=True, exist_ok=True)
    account = load_account(args.account)
    tag = f"warm-{account['login']}-{time.strftime('%H%M%S')}"

    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled", "--no-first-run",
                  "--window-size=1366,900"],
        )
        context = await browser.new_context(
            user_agent=UA, locale="ru-RU", timezone_id="Europe/Moscow",
            viewport={"width": 1366, "height": 900}, proxy=proxy_conf(args.port),
        )
        await context.add_init_script(STEALTH)
        page = await context.new_page()

        print(f"[1/4] окно открыто, аккаунт {account['login']}, прокси порт {args.port}", flush=True)
        await page.goto("https://www.roblox.com/login", wait_until="domcontentloaded", timeout=90_000)
        await page.wait_for_timeout(2500)
        await page.evaluate(
            "() => document.querySelectorAll('[class*=cookie-banner], .cookie-banner-bg')"
            ".forEach(e => e.remove())"
        )
        await page.fill("#login-username", account["login"], timeout=25_000)
        await page.fill("#login-password", account["password"], timeout=25_000)
        await page.click("#login-button", timeout=20_000)
        print("[2/4] форма отправлена. ЖДУ ЧЕЛОВЕКА: удерживай кнопку в окне", flush=True)

        session_cookies: dict[str, str] = {}
        deadline = time.monotonic() + args.wait
        announced = False
        while time.monotonic() < deadline:
            await page.wait_for_timeout(1500)
            cookies = await context.cookies()
            session = [c for c in cookies if c.get("name") == ".ROBLOSECURITY" and c.get("value")]
            if session:
                session_cookies = {c["name"]: c["value"] for c in cookies}
                print(f"[3/4] СЕССИЯ ПОЛУЧЕНА, длина {len(session[0]['value'])}, "
                      f"cookie {len(cookies)} шт", flush=True)
                break
            if not announced:
                state = await page.evaluate(
                    "() => document.body.innerText.match(/Hold the button|Verify you|Reference ID/i)?.[0] || ''"
                )
                if state:
                    print(f"    на странице: {state}", flush=True)
                    announced = True

        await page.screenshot(path=str(ART / f"{tag}.png"))

        if not session_cookies:
            print("[!] сессии нет за отведённое время — окно закрывается", flush=True)
            await browser.close()
            return 2

        (VAR / f"session-{account['login']}.json").write_text(
            json.dumps(session_cookies, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"    cookie-банка: var/session-{account['login']}.json", flush=True)

        identity = None
        try:
            resp = await context.request.get(
                "https://users.roblox.com/v1/users/authenticated", timeout=25_000
            )
            body = await resp.text()
            identity = {"status": resp.status, "body": body[:300]}
            print(f"[4/4] контроль в браузере: {resp.status} {body[:160]}", flush=True)
        except Exception as exc:
            print(f"[4/4] контроль не выполнен: {type(exc).__name__}", flush=True)

        await browser.close()

    print("--- повторный вход обычным HTTP-клиентом с этой же банкой ---", flush=True)
    replay = await http_replay(session_cookies, args.port, account)
    print(json.dumps(replay, ensure_ascii=False, indent=2), flush=True)

    (VAR / "warmup-result.json").write_text(
        json.dumps({"account": account["login"], "port": args.port,
                    "identity": identity, "http_replay": replay},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    gate_lifted = (
        replay.get("status") in (200, 201)
        or replay.get("cls") == "SUCCESS"
        or "challenge is required" not in replay.get("body", "").lower()
    )
    print(f"\nВЫВОД: гейт {'снят' if gate_lifted else 'держится'} для повторного HTTP-входа",
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
