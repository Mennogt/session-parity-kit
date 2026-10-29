"""Разбор устройства модалки PerimeterX: что именно надо удерживать.

Платформа отдаёт #px-captcha, внутри — кнопка удержания. Разбираем дерево,
чтобы понять точку приложения усилия: элемент, его бокс, обработчики.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from probes.m1_browser import load_account, proxy_conf
from probes.proxy_check import USER  # noqa: F401  (единый источник адреса)
import sys

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

DUMP_JS = r"""
() => {
  const box = (el) => {
    try {
      const b = el.getBoundingClientRect();
      return [Math.round(b.x), Math.round(b.y), Math.round(b.width), Math.round(b.height)];
    } catch (e) { return null; }
  };
  const hits = [];
  for (const el of document.querySelectorAll('*')) {
    let t = '';
    try { t = (el.innerText || '').trim(); } catch (e) { t = ''; }
    if (t && t.length < 300 && /Verify you|удерживай|Hold the button|human/i.test(t)) {
      const chain = [];
      let n = el;
      while (n && chain.length < 6) { chain.push(n.tagName + (n.id ? '#' + n.id : '')); n = n.parentElement; }
      hits.push({tag: el.tagName, id: el.id || null, text: t.slice(0, 120), box: box(el), chain});
    }
  }
  const frames = [];
  for (const f of document.querySelectorAll('iframe')) {
    frames.push({src: (f.src || '').slice(0, 120), id: f.id || null, box: box(f)});
  }
  // Кандидаты на «кнопку удержания»: div/button с обработчиками и заметным боксом.
  const targets = [];
  for (const el of document.querySelectorAll('div, button, span')) {
    const b = box(el);
    if (!b || b[2] < 80 || b[3] < 20) continue;
    if (b[2] > 600 || b[3] > 200) continue;
    const st = getComputedStyle(el);
    if (st.cursor === 'pointer' || el.getAttribute('role') === 'button') {
      targets.push({tag: el.tagName, id: el.id || null, box: b,
                    text: ((el.innerText || '') + '').trim().slice(0, 80), cursor: st.cursor});
    }
  }
  return {hits: hits.slice(0, 10), frames, targets: targets.slice(0, 25)};
}
"""


async def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 10022
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
        # Куки-баннер перекрывает форму и перехватывает клики.
        await page.evaluate(
            "() => { for (const sel of ['#cookie-banner-wrapper', '.cookie-banner-bg',"
            " '[class*=cookie-banner]']) { document.querySelectorAll(sel)"
            ".forEach(e => e.remove()); } }"
        )
        await page.wait_for_timeout(300)
        await page.fill("#login-username", account["login"])
        await page.fill("#login-password", account["password"])
        await page.click("#login-button")
        for _ in range(30):
            await page.wait_for_timeout(1000)
            if await page.evaluate("() => !!document.querySelector('#px-captcha')"):
                break
        await page.wait_for_timeout(2000)
        dump = await page.evaluate(DUMP_JS)
        Path("fixtures/browser/px-dump.json").write_text(
            json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("=== СОВПАДЕНИЯ ПО ТЕКСТУ ===")
        for h in dump.get("hits", []):
            print(f"  {h['tag']:<8} id={h['id']} box={h['box']}")
            print(f"    chain: {' < '.join(h['chain'])}")
            print(f"    text : {h['text']!r}")
        print("=== IFRAME ===")
        for f in dump.get("frames", []):
            print(f"  {f['id']} {f['box']} {f['src']}")
        print("=== КЛИКАБЕЛЬНЫЕ КАНДИДАТЫ ===")
        for t in dump.get("targets", []):
            print(f"  {t['tag']:<7} id={t['id']} box={t['box']} cursor={t['cursor']} text={t['text']!r}")
        await browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
