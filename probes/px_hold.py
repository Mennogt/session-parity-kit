"""Прохождение проверки «нажмите и удерживайте» (PerimeterX / HUMAN).

Платформа показывает кнопку, которую надо удерживать. Скрипт находит её по
подписи, ставит курсор в центр и держит, добавляя естественный дрейф, затем
отпускает и ждёт результата: либо модалка исчезает и вход продолжается,
либо проверка повторяется.

    python -m probes.px_hold --account 0 --port 10030

Всё пишется в fixtures/browser/: скриншоты на каждом этапе, сетевые события.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
from pathlib import Path

from probes.m1_browser import STEALTH, UA, load_account, proxy_conf

ART = Path("fixtures/browser")
HOLD_LABELS = (
    "удерживай",
    "hold",
    "press and hold",
    "нажмите",
)

FIND_HOLD = r"""
(labels) => {
  const box = (el) => {
    const b = el.getBoundingClientRect();
    return {x: b.x, y: b.y, w: b.width, h: b.height};
  };
  const cands = [];
  for (const el of document.querySelectorAll('button, div, span, a')) {
    let t = '';
    try { t = ((el.innerText || '') + '').trim().toLowerCase(); } catch (e) { continue; }
    if (!t || t.length > 80) continue;
    if (!labels.some(l => t.includes(l))) continue;
    const b = el.getBoundingClientRect();
    if (b.width < 60 || b.height < 20) continue;
    cands.push({tag: el.tagName, id: el.id || null, text: t, box: box(el),
                cls: (el.className || '').toString().slice(0, 60)});
  }
  return cands.sort((a, b) => a.box.w * a.box.h - b.box.w * b.box.h).slice(0, 6);
}
"""

CHALLENGE_STATE = r"""
() => {
  const txt = document.body.innerText || '';
  const dialog = document.querySelector('#radix-0, [role=dialog]');
  return {
    hasDialog: !!dialog,
    holding: /Verify you|Hold the button|удерживай/i.test(txt),
    reference: (txt.match(/Reference ID\s+([0-9a-f-]{20,})/i) || [])[1] || null,
    hint: (txt.match(/.{0,30}(hold|удерживай|confirm).{0,60}/i) || [])[0] || null,
    cookies: document.cookie.split(';').map(c => c.split('=')[0].trim()).filter(Boolean),
  };
}
"""


async def hold(page, box: dict, seconds: float) -> None:  # noqa: ANN001
    """Удержание с дрейфом, похожим на живую руку."""
    cx = box["x"] + box["w"] / 2
    cy = box["y"] + box["h"] / 2
    await page.mouse.move(cx, cy, steps=8)
    await page.wait_for_timeout(random.randint(90, 180))
    await page.mouse.down()
    started = time.monotonic()
    last_log = 0.0
    while time.monotonic() - started < seconds:
        await page.wait_for_timeout(120)
        # Небольшой дрейф в пределах кнопки: живая рука не стоит на месте.
        await page.mouse.move(
            cx + random.uniform(-2.5, 2.5), cy + random.uniform(-2.0, 2.0)
        )
        elapsed = time.monotonic() - started
        if elapsed - last_log > 1.0:
            last_log = elapsed
            print(f"    удержание {elapsed:.1f}с", flush=True)
    await page.mouse.up()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, default=0)
    ap.add_argument("--port", type=int, default=10030)
    ap.add_argument("--hold", type=float, default=11.0)
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--rounds", type=int, default=3)
    args = ap.parse_args()

    ART.mkdir(parents=True, exist_ok=True)
    tag = f"hold-a{args.account}-p{args.port}-{time.strftime('%H%M%S')}"
    account = load_account(args.account)

    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=args.headless,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled", "--no-first-run"],
        )
        context = await browser.new_context(
            user_agent=UA, locale="ru-RU", timezone_id="Europe/Moscow",
            viewport={"width": 1366, "height": 900}, proxy=proxy_conf(args.port),
        )
        await context.add_init_script(STEALTH)
        page = await context.new_page()
        net: list[dict] = []
        page.on("response", lambda r: net.append({"url": r.url[:140], "status": r.status}))

        print(f"аккаунт {account['login']}, порт {args.port}")
        await page.goto("https://www.roblox.com/login", wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(2200)
        await page.evaluate(
            "() => document.querySelectorAll('[class*=cookie-banner], .cookie-banner-bg')"
            ".forEach(e => e.remove())"
        )
        await page.fill("#login-username", account["login"])
        await page.fill("#login-password", account["password"])
        await page.click("#login-button")
        print("форма отправлена")

        outcome = "неизвестно"
        for attempt in range(1, args.rounds + 1):
            # Ждём либо кабинет, либо кнопку удержания.
            found = None
            for _ in range(60):
                await page.wait_for_timeout(1000)
                if "/home" in page.url or "discover" in page.url:
                    outcome = "вошли, редирект в кабинет"
                    break
                cands = await page.evaluate(FIND_HOLD, list(HOLD_LABELS))
                if cands:
                    found = cands[0]
                    break
            if outcome.startswith("вошли"):
                break
            if not found:
                state = await page.evaluate(CHALLENGE_STATE)
                await page.screenshot(path=str(ART / f"{tag}-r{attempt}-nobtn.png"))
                print(f"раунд {attempt}: кнопка не найдена; состояние={state}")
                outcome = "кнопка не найдена"
                continue

            print(f"раунд {attempt}: кнопка {found['tag']} id={found['id']} "
                  f"box={found['box']} text={found['text']!r}")
            await page.screenshot(path=str(ART / f"{tag}-r{attempt}-before.png"))
            await hold(page, found["box"], args.hold)
            print("    отпустил, ждём решения")

            for _ in range(30):
                await page.wait_for_timeout(1000)
                if "/home" in page.url or "discover" in page.url:
                    outcome = "проверка пройдена, вошли"
                    break
                state = await page.evaluate(CHALLENGE_STATE)
                if not state["hasDialog"] and not state["holding"]:
                    outcome = "модалка ушла, но редиректа нет"
                    break
            await page.screenshot(path=str(ART / f"{tag}-r{attempt}-after.png"))
            if outcome.startswith("проверка пройдена"):
                break
            print(f"    после раунда {attempt}: {outcome}")

        cookies = await context.cookies()
        names = sorted({c["name"] for c in cookies})
        identity = None
        try:
            resp = await context.request.get(
                "https://users.roblox.com/v1/users/authenticated", timeout=20_000
            )
            if resp.ok:
                identity = {"status": resp.status, "body": (await resp.text())[:400]}
        except Exception as exc:
            print("контроль не выполнен:", type(exc).__name__)

        (ART / f"{tag}-net.json").write_text(
            json.dumps(net, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("-" * 60)
        print(f"итог:      {outcome}")
        print(f"url:       {page.url}")
        print(f"cookie:    {names}")
        print(f"контроль:  {identity}")
        print(f"снимки:    {ART}\\{tag}-*.png")
        if identity:
            print("СЕССИЯ ПОДТВЕРЖДЕНА")
        await browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
