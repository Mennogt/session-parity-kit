"""Печать HTML модалки проверки: нужен точный узел удержания."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from probes.m1_browser import load_account, proxy_conf

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


async def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 10026
    from playwright.async_api import async_playwright

    account = load_account(0)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, channel="chrome")
        context = await browser.new_context(
            user_agent=UA, locale="ru-RU", timezone_id="Europe/Moscow",
            viewport={"width": 1366, "height": 900}, proxy=proxy_conf(port),
        )
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
        for _ in range(30):
            await page.wait_for_timeout(1000)
            if await page.evaluate("() => !!document.querySelector('#radix-0')"):
                break
        await page.wait_for_timeout(2500)
        html = await page.evaluate("() => { const r = document.querySelector('#radix-0'); return r ? r.outerHTML : '(нет)'; }")
        Path("fixtures/browser/px-radix.html").write_text(html, encoding="utf-8")
        print(html[:4000])
        # Что за элемент внутри: все потомки с боксом
        nodes = await page.evaluate(
            """() => {
              const out = [];
              const root = document.querySelector('#radix-0');
              if (!root) return out;
              for (const el of root.querySelectorAll('*')) {
                const b = el.getBoundingClientRect();
                out.push({tag: el.tagName, id: el.id || null,
                          cls: (el.className || '').toString().slice(0, 50),
                          role: el.getAttribute('role'),
                          box: [Math.round(b.x), Math.round(b.y), Math.round(b.width), Math.round(b.height)],
                          text: ((el.innerText || '') + '').slice(0, 40)});
              }
              return out;
            }"""
        )
        Path("fixtures/browser/px-nodes.json").write_text(
            json.dumps(nodes, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("=== УЗЛЫ ===")
        for n in nodes:
            print(f"  {n['tag']:<7} id={n['id']} role={n['role']} box={n['box']} text={n['text']!r}")
        await browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
