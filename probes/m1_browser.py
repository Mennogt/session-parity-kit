"""M1 через настоящий браузерный контекст.

Зачем: обмен парой из-под HTTP-клиента платформа встречает требованием
проверки («Challenge is required»), но **не** отдаёт captchaId. Значит
прохождение узла привязано к браузерному контексту: скрипт Arkose грузится
страницей входа, получает свой идентификатор сессии и только после этого
обмен принимается.

Скрипт открывает страницу входа в Chromium через прокси профиля, вводит
пару и наблюдает, что происходит: показ узла, редирект в кабинет, отказ.

    python -m probes.m1_browser --account 0 --port 10000 --headless

Результат: статус, cookie, скриншот, набор сетевых событий для разбора.
Узел проверки, если он интерактивный, требует прохождения — скрипт даёт
ему время и сообщает, что именно показано.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from urllib.parse import urlparse

from core.logging import get_logger, setup_logging
from probes.proxy_check import HOST, PASSWORD, USER

logr = get_logger("m1.browser")
ACCOUNTS = Path("var/accounts.json")
ARTIFACTS = Path("fixtures/browser")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Минимальный набор признаков обычного браузера: без него автоматизация
# видна по navigator.webdriver и по отсутствию плагинов.
STEALTH = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU', 'ru', 'en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || {runtime: {}};
const origQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (p) =>
  p.name === 'notifications' ? Promise.resolve({state: Notification.permission}) : origQuery(p);
"""


def proxy_conf(port: int) -> dict:
    return {
        "server": f"http://{HOST}:{port}",
        "username": USER,
        "password": PASSWORD,
        "bypass": "localhost,127.0.0.1",
    }


def load_account(index: int) -> dict:
    data = json.loads(ACCOUNTS.read_text(encoding="utf-8"))
    if not 0 <= index < len(data):
        raise SystemExit(f"аккаунт #{index} вне диапазона")
    return data[index]


async def fetch_identity(context) -> dict | None:  # noqa: ANN001
    """Контрольный запрос из того же контекста — cookie едут автоматически."""
    for url in (
        "https://users.roblox.com/v1/users/authenticated",
        "https://www.roblox.com/mobileapi/userinfo",
    ):
        try:
            resp = await context.request.get(url, timeout=20_000)
            if resp.ok:
                return {"url": url, "status": resp.status, "body": await resp.text()[:500]}
            logr.info("контроль %s -> %s", url, resp.status)
        except Exception as exc:
            logr.info("контроль %s -> ошибка %s", url, type(exc).__name__)
    return None


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, default=0)
    ap.add_argument("--port", type=int, default=10000)
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--channel", default="chrome", help="системный браузер: chrome | msedge | пусто = сборка playwright")
    ap.add_argument("--wait", type=float, default=120.0)
    ap.add_argument("--no-proxy", action="store_true")
    args = ap.parse_args()

    setup_logging("INFO", as_json=False)
    account = load_account(args.account)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%H%M%S")
    tag = f"a{args.account}-p{args.port}-{stamp}"

    from playwright.async_api import async_playwright

    events: list[dict] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=args.headless,
            channel=args.channel or None,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        )
        context = await browser.new_context(
            user_agent=UA,
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            viewport={"width": 1366, "height": 900},
            proxy=None if args.no_proxy else proxy_conf(args.port),
        )
        await context.add_init_script(STEALTH)
        page = await context.new_page()

        page.on(
            "response",
            lambda r: events.append(
                {"phase": "response", "url": r.url, "status": r.status}
            )
            if "/v2/login" in r.url or "arkose" in r.url or "captcha" in r.url
            else None,
        )
        page.on(
            "framenavigated",
            lambda f: events.append(
                {"phase": "navigate", "url": f.url}
            )
            if f == page.main_frame
            else None,
        )

        print(f"аккаунт {account['login']} / порт {args.port} / headless={args.headless}")
        await page.goto("https://www.roblox.com/login", wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(2500)

        try:
            await page.fill("#login-username", account["login"], timeout=20_000)
            await page.fill("#login-password", account["password"], timeout=20_000)
        except Exception as exc:
            await page.screenshot(path=str(ARTIFACTS / f"{tag}-form-fail.png"))
            print(f"форма не найдена: {type(exc).__name__}: {exc}")
            await browser.close()
            return 2

        await page.click("#login-button", timeout=15_000)

        deadline = time.monotonic() + args.wait
        outcome = "неизвестно"
        challenge_seen = False
        while time.monotonic() < deadline:
            await page.wait_for_timeout(1000)
            url = page.url
            if "/home" in url or "discover" in url:
                outcome = "редирект в кабинет"
                break
            if not challenge_seen:
                challenge_seen = await page.evaluate(
                    "() => !!document.querySelector('#funcaptcha-modal-body') || "
                    "!!document.querySelector('iframe[src*=\"arkose\"]') || "
                    "!!document.querySelector('#px-captcha') || "
                    "!!document.querySelector('iframe[src*=\"px-cdn\"]') || "
                    "document.body.innerText.includes('Verify you')"
                )
                if challenge_seen:
                    print("[!] узел проверки показан")
                    dump = await page.evaluate(
                        """() => {
                          const btns = [...document.querySelectorAll('button, [role=button]')]
                            .map(b => ({tag: b.tagName, id: b.id, cls: b.className,
                                        text: (b.innerText || '').slice(0, 60)}))
                            .filter(b => b.text || b.id);
                          const ifr = [...document.querySelectorAll('iframe')]
                            .map(f => f.src || f.id || '(пусто)');
                          const hosts = [...document.querySelectorAll('*')]
                            .filter(e => e.shadowRoot).map(e => e.tagName + '#' + e.id);
                          return {btns, ifr, hosts,
                                  hasPxCaptcha: !!document.querySelector('#px-captcha'),
                                  bodyHint: document.body.innerText.match(/.{0,40}(удерживай|Hold|human).{0,60}/i)?.[0] || null};
                        }"""
                    )
                    print("    кнопки:", dump["btns"][:8])
                    print("    iframe:", dump["ifr"][:8])
                    print("    shadow:", dump["hosts"][:8])
                    print("    px-captcha:", dump["hasPxCaptcha"], "| подсказка:", dump["bodyHint"])
            err = await page.evaluate(
                "() => { const e = document.querySelector('#login-form-error, .form-error, "
                ".text-danger'); return e ? e.innerText : null; }"
            )
            if err:
                outcome = f"ошибка формы: {err.strip()[:160]}"
                break

        cookies = await context.cookies()
        names = sorted({c["name"] for c in cookies})
        identity = await fetch_identity(context)

        await page.screenshot(path=str(ARTIFACTS / f"{tag}.png"))
        (ARTIFACTS / f"{tag}-events.json").write_text(
            json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        print("-" * 60)
        print(f"итог:        {outcome}")
        print(f"узел:        {'показан' if challenge_seen else 'не показан'}")
        print(f"url:         {page.url}")
        print(f"cookie:      {names}")
        print(f"контроль:    {identity}")
        print(f"скриншот:    {ARTIFACTS / f'{tag}.png'}")
        print(f"события:     {len(events)} шт")

        if identity:
            print("СЕССИЯ ПОДТВЕРЖДЕНА")
            (ARTIFACTS / f"{tag}-identity.json").write_text(
                json.dumps(identity, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        await browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
