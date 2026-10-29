"""Попытка пройти проверку «удерживайте» без поиска по тексту.

Кнопка удержания рисуется внутри диалога проверки, но её текст не всегда
доступен как отдельный узел. Поэтому точка нажатия берётся от бокса самого
диалога: центр по горизонтали, нижняя треть по вертикали.

Скрипт перебирает порты прокси, потому что проверка показывается по
риск-решению и на части адресов не появляется вовсе.

    python -m probes.px_solve --ports 10040,10041,10042 --hold 10
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

DIALOG_BOX = r"""
() => {
  const box = (el) => {
    const b = el.getBoundingClientRect();
    return {x: b.x, y: b.y, w: b.width, h: b.height};
  };
  const dialogs = [...document.querySelectorAll('[role=dialog], #px-captcha, [id^=radix]')]
    .map(el => ({el, b: box(el)}))
    .filter(d => d.b.w > 200 && d.b.h > 80);
  if (dialogs.length) {
    dialogs.sort((a, b) => a.b.w * a.b.h - b.b.w * b.b.h);
    const d = dialogs[0];
    return {found: true, box: d.b, id: d.el.id || null, role: d.el.getAttribute('role'),
            text: ((d.el.innerText || '') + '').slice(0, 120)};
  }
  const txt = document.body.innerText || '';
  const m = txt.match(/Reference ID\s+([0-9a-f-]{20,})/i);
  if (m) return {found: false, reference: m[1], text: txt.slice(0, 200)};
  return {found: false};
}
"""


async def attempt(port: int, account: dict, hold_seconds: float, headed: bool, tag: str) -> dict:
    from playwright.async_api import async_playwright

    result: dict = {"port": port, "outcome": "неизвестно"}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=not headed,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled", "--no-first-run"],
        )
        context = await browser.new_context(
            user_agent=UA, locale="ru-RU", timezone_id="Europe/Moscow",
            viewport={"width": 1366, "height": 900}, proxy=proxy_conf(port),
        )
        await context.add_init_script(STEALTH)
        page = await context.new_page()
        try:
            await page.goto("https://www.roblox.com/login", wait_until="domcontentloaded", timeout=55_000)
            await page.wait_for_timeout(2200)
            await page.evaluate(
                "() => document.querySelectorAll('[class*=cookie-banner], .cookie-banner-bg')"
                ".forEach(e => e.remove())"
            )
            await page.fill("#login-username", account["login"], timeout=20_000)
            await page.fill("#login-password", account["password"], timeout=20_000)
            await page.click("#login-button", timeout=15_000)

            dialog = {"found": False}
            for _ in range(45):
                await page.wait_for_timeout(1000)
                if "/home" in page.url or "discover" in page.url:
                    result["outcome"] = "вошли без проверки"
                    break
                dialog = await page.evaluate(DIALOG_BOX)
                if dialog.get("found") or dialog.get("reference"):
                    break

            if result["outcome"] == "вошли без проверки":
                pass
            elif not dialog.get("found"):
                result["outcome"] = f"проверки нет ({dialog.get('reference', 'нет диалога')})"
            else:
                box = dialog["box"]
                cx = box["x"] + box["w"] / 2
                cy = box["y"] + box["h"] * 0.60
                result["dialog"] = {"id": dialog.get("id"), "box": [round(v) for v in
                                    (box["x"], box["y"], box["w"], box["h"])], "point": [round(cx), round(cy)]}
                await page.screenshot(path=str(ART / f"{tag}-before.png"))
                await page.mouse.move(cx, cy, steps=10)
                await page.wait_for_timeout(random.randint(120, 260))
                await page.mouse.down()
                t0 = time.monotonic()
                while time.monotonic() - t0 < hold_seconds:
                    await page.wait_for_timeout(110)
                    await page.mouse.move(cx + random.uniform(-2.5, 2.5),
                                          cy + random.uniform(-2.0, 2.0))
                await page.mouse.up()
                result["held"] = round(time.monotonic() - t0, 1)

                # Критерий — не состояние DOM, а факт появления сессии.
                # Диалог может исчезать и появляться снова, пока идёт обмен.
                for tick in range(75):
                    await page.wait_for_timeout(1000)
                    cookies_now = await context.cookies()
                    session = [
                        c for c in cookies_now
                        if c["name"] == ".ROBLOSECURITY" and c.get("value")
                    ]
                    if session:
                        result["outcome"] = "сессионная cookie получена"
                        result["session_len"] = len(session[0]["value"])
                        break
                    if "/home" in page.url or "discover" in page.url:
                        result["outcome"] = "вошли, редирект в кабинет"
                        break
                    err = await page.evaluate(
                        "() => {"
                        "  const sels = ['#login-form-error', '.form-error',"
                        "                '[class*=error-message]', '[role=alert]'];"
                        "  for (const s of sels) {"
                        "    let e = null;"
                        "    try { e = document.querySelector(s); } catch (x) { continue; }"
                        "    if (!e) continue;"
                        "    const t = (e.innerText || '').trim();"
                        "    if (t) return t.slice(0, 200);"
                        "  }"
                        "  return null;"
                        "}"
                    )
                    if err:
                        result["outcome"] = f"ошибка формы: {err}"
                        result["form_error"] = err
                        break
                    if tick % 20 == 19:
                        print(f"    ...{tick + 1}с, url={page.url[:60]}, cookie={len(cookies_now)}")
                else:
                    result["outcome"] = "нет реакции за 75с"

            await page.screenshot(path=str(ART / f"{tag}-after.png"))
            cookies = await context.cookies()
            result["cookies"] = sorted({c["name"] for c in cookies})
            try:
                resp = await context.request.get(
                    "https://users.roblox.com/v1/users/authenticated", timeout=20_000
                )
                result["identity_status"] = resp.status
                if resp.ok:
                    result["identity"] = (await resp.text())[:300]
            except Exception as exc:
                result["identity_error"] = type(exc).__name__
        except Exception as exc:
            result["outcome"] = f"сбой: {type(exc).__name__}: {str(exc)[:120]}"
        finally:
            await browser.close()
    return result


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ports", default="10040,10041,10042")
    ap.add_argument("--account", type=int, default=0)
    ap.add_argument("--hold", type=float, default=10.0)
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    ART.mkdir(parents=True, exist_ok=True)
    account = load_account(args.account)
    ports = [int(p) for p in args.ports.split(",") if p.strip()]
    print(f"аккаунт {account['login']}, портов: {len(ports)}, headed={args.headed}")

    results = []
    for port in ports:
        tag = f"solve-p{port}-{time.strftime('%H%M%S')}"
        print(f"\n--- порт {port} ---")
        r = await attempt(port, account, args.hold, args.headed, tag)
        results.append(r)
        print(f"  итог: {r.get('outcome')}")
        print(f"  точка нажатия: {r.get('dialog')}")
        print(f"  удержание: {r.get('held')}с")
        print(f"  контроль: {r.get('identity_status')} {r.get('identity', '')[:80]}")

    (ART / "px-solve-results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ok = [r for r in results if r.get("outcome") == "проверка пройдена"]
    print(f"\nпройдено: {len(ok)} из {len(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
