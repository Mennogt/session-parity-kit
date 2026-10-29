"""Сколько стоит один браузерный поток: байты и секунды.

Мерим через CDP Network.loadingFinished (поле encodedDataLength — реально
переданные байты, без распаковки), в двух режимах:

  full    — как обычный браузер: картинки, шрифты, медиа
  lean    — картинки/медиа/шрифты заблокированы (страница и скрипты проверки
            остаются работоспособными)

    python -m probes.traffic --port 10050
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

ART = Path("fixtures/browser")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
BLOCK = {"image", "media", "font"}


def proxy_conf(port: int) -> dict:
    from probes.m1_browser import proxy_conf as conf

    return conf(port)


async def measure(port: int, lean: bool, wait_s: float) -> dict:
    from playwright.async_api import async_playwright

    stats = {"bytes": 0, "requests": 0, "by_type": {}}
    t0 = time.perf_counter()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, channel="chrome")
        context = await browser.new_context(
            user_agent=UA, locale="ru-RU", timezone_id="Europe/Moscow",
            viewport={"width": 1366, "height": 900}, proxy=proxy_conf(port),
        )
        if lean:
            async def handler(route, request):  # noqa: ANN001
                if request.resource_type in BLOCK:
                    await route.abort()
                else:
                    await route.continue_()

            await context.route("**/*", handler)

        page = await context.new_page()
        cdp = await context.new_cdp_session(page)
        await cdp.send("Network.enable")

        types: dict[str, str] = {}

        def on_request(params: dict) -> None:
            types[params["requestId"]] = params.get("type", "Other")

        def on_finished(params: dict) -> None:
            size = params.get("encodedDataLength") or 0
            stats["bytes"] += size
            stats["requests"] += 1
            kind = types.get(params["requestId"], "Other")
            stats["by_type"][kind] = stats["by_type"].get(kind, 0) + size

        cdp.on("Network.requestWillBeSent", lambda p: on_request(p))
        cdp.on("Network.loadingFinished", lambda p: on_finished(p))

        await page.goto("https://www.roblox.com/login", wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(int(wait_s * 1000))
        dom_ready = time.perf_counter() - t0
        await browser.close()

    stats["mode"] = "lean" if lean else "full"
    stats["seconds_to_dom"] = round(dom_ready, 2)
    stats["kib"] = round(stats["bytes"] / 1024, 1)
    stats["mib"] = round(stats["bytes"] / 1024 / 1024, 2)
    stats["by_type_mib"] = {
        k: round(v / 1024 / 1024, 2) for k, v in sorted(
            stats["by_type"].items(), key=lambda kv: -kv[1]
        )
    }
    return stats


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=10050)
    ap.add_argument("--wait", type=float, default=6.0)
    args = ap.parse_args()

    ART.mkdir(parents=True, exist_ok=True)
    results = []
    for lean in (False, True):
        r = await measure(args.port, lean, args.wait)
        results.append(r)
        print(f"[{r['mode']:<4}] запросов={r['requests']:<4} "
              f"трафик={r['mib']:>6.2f} МиБ  до DOM={r['seconds_to_dom']}с")
        print(f"        по типам: {r['by_type_mib']}")

    full, lean = results[0], results[1]
    if lean["bytes"]:
        print(f"экономия: {100 - lean['bytes'] / full['bytes'] * 100:.0f}%")
    (ART / "traffic.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Арифметика под требование 1000/мин
    for r in results:
        per_min_mib = r["mib"] * 1000
        print(f"{r['mode']}: на 1000 потоков/мин -> {per_min_mib / 1024:.1f} ГиБ/мин "
              f"= {per_min_mib * 60 / 1024:.0f} ГиБ/час")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
