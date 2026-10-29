"""Где именно живёт кнопка удержания: главный документ, iframe или shadow DOM."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from probes.m1_browser import STEALTH, UA, load_account, proxy_conf

ART = Path("fixtures/browser")


async def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 10031
    from playwright.async_api import async_playwright

    account = load_account(0)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, channel="chrome")
        context = await browser.new_context(
            user_agent=UA, locale="ru-RU", timezone_id="Europe/Moscow",
            viewport={"width": 1366, "height": 900}, proxy=proxy_conf(port),
        )
        await context.add_init_script(STEALTH)
        page = await context.new_page()
        await page.goto("https://www.roblox.com/login", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        await page.evaluate(
            "() => document.querySelectorAll('[class*=cookie-banner], .cookie-banner-bg')"
            ".forEach(e => e.remove())"
        )
        await page.fill("#login-username", account["login"])
        await page.fill("#login-password", account["password"])
        await page.click("#login-button")
        print("ждём появления проверки...")
        for _ in range(45):
            await page.wait_for_timeout(1000)
            txt = await page.evaluate("() => document.body.innerText || ''")
            if "Hold the button" in txt or "удерживай" in txt.lower():
                print("текст проверки найден в главном документе")
                break

        report = {"frames": [], "shadow": [], "match": None}

        for fr in page.frames:
            try:
                ftxt = await fr.evaluate("() => (document.body && document.body.innerText) || ''")
            except Exception as exc:
                ftxt = f"<ошибка: {type(exc).__name__}>"
            has = ("Hold the button" in ftxt) or ("удерживай" in ftxt.lower())
            report["frames"].append({"url": fr.url[:120], "has_hold": has, "len": len(ftxt)})

        # Ищем текстовый узел и печатаем путь до него.
        match = await page.evaluate(
            r"""() => {
              const out = [];
              const walk = (root, path) => {
                for (const el of root.querySelectorAll('*')) {
                  let t = '';
                  try { t = (el.innerText || '').trim(); } catch (e) { t = ''; }
                  if (t && t.length < 120 && /удерживай|hold the button/i.test(t)) {
                    const b = el.getBoundingClientRect();
                    out.push({path, tag: el.tagName, id: el.id || null,
                              box: [Math.round(b.x), Math.round(b.y), Math.round(b.width), Math.round(b.height)],
                              text: t, html: el.outerHTML.slice(0, 300)});
                  }
                  if (el.shadowRoot) walk(el.shadowRoot, path + '>shadow(' + el.tagName + ')');
                }
              };
              walk(document, 'doc');
              return out.slice(0, 10);
            }"""
        )
        report["match"] = match

        # Все shadow-хосты и их содержимое
        shadow = await page.evaluate(
            """() => {
              const out = [];
              const walk = (root) => {
                for (const el of root.querySelectorAll('*')) {
                  if (el.shadowRoot) {
                    out.push({host: el.tagName + (el.id ? '#' + el.id : ''),
                              html: el.shadowRoot.innerHTML.slice(0, 500)});
                    walk(el.shadowRoot);
                  }
                }
              };
              walk(document);
              return out.slice(0, 8);
            }"""
        )
        report["shadow"] = shadow

        ART.mkdir(parents=True, exist_ok=True)
        (ART / "px-where.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("=== FRAMES ===")
        for f in report["frames"]:
            print(f"  {f['url']} hold={f['has_hold']} len={f['len']}")
        print("=== СОВПАДЕНИЯ (с путём) ===")
        for m in report["match"]:
            print(f"  {m['path']} {m['tag']} id={m['id']} box={m['box']} text={m['text']!r}")
            print(f"    {m['html'][:200]}")
        print("=== SHADOW ===")
        for s in report["shadow"]:
            print(f"  {s['host']}: {s['html'][:200]}")
        await browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
