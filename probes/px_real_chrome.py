"""Токен PerimeterX от настоящего Chrome, а не от автоматизированного.

Разница принципиальная: токен, выданный браузером под управлением Playwright,
может быть низкого доверия. Здесь браузер живёт сам, без флагов автоматизации;
Playwright подключается к нему по CDP **только чтобы прочитать cookie**, уже
после того как токен выдан.

Затем эти cookie подставляются в обмен пары обычным HTTP-клиентом:
  * контроль — прогретый аккаунт (должен пройти);
  * опыт — свежий аккаунт (вопрос: снимает ли настоящий токен гейт).

    python -m probes.px_real_chrome --fresh 3 --warm 0
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

import httpx

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
VAR = Path("var")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
LOGIN_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://www.roblox.com",
    "Referer": "https://www.roblox.com/login",
    "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def verdict(status: int, body: str) -> str:
    low = body.lower()
    if "token validation failed" in low:
        return "csrf-only"
    if "challenge is required" in low:
        return "GATE"
    if "incorrect username or password" in low:
        return "NO GATE"
    if status == 200:
        return "SUCCESS"
    return f"other:{status}"


async def login(login_name: str, password: str, cookies: dict[str, str]) -> dict:
    body = {"ctype": "Username", "cvalue": login_name, "password": password}
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=20, read=40, write=40, pool=20),
        follow_redirects=False, cookies=cookies,
    ) as client:
        r = await client.post("https://auth.roblox.com/v2/login", headers=LOGIN_HEADERS, json=body)
        token = r.headers.get("x-csrf-token")
        if token:
            headers = dict(LOGIN_HEADERS)
            headers["X-CSRF-TOKEN"] = token
            r = await client.post("https://auth.roblox.com/v2/login", headers=headers, json=body)
        return {
            "status": r.status_code,
            "verdict": verdict(r.status_code, r.text),
            "body": r.text[:180],
            "session_len": len(next((c.value for c in client.cookies.jar
                                     if c.name == ".ROBLOSECURITY"), "")),
        }


async def harvest(url: str, wait: float, port: int | None = None) -> dict[str, str]:
    """Запустить настоящий Chrome, дать защите выдать токен, забрать cookie."""
    port = port or free_port()
    profile = (VAR / f"chrome-px-{int(time.time())}").resolve()
    profile.mkdir(parents=True, exist_ok=True)
    log_path = VAR / "chrome-stderr.log"

    with open(log_path, "wb") as errlog:
        proc = subprocess.Popen(
            [
                CHROME,
                f"--remote-debugging-port={port}",
                "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=1200,800",
                "--window-position=60,60",
                url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=errlog,
        )

    # Ждём подъёма DevTools.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.get(f"http://127.0.0.1:{port}/json/version")
            if r.status_code == 200:
                break
        except Exception:
            pass
        await asyncio.sleep(0.5)
    else:
        alive = proc.poll() is None
        tail = ""
        try:
            tail = log_path.read_text(errors="replace")[-400:]
        except Exception:
            pass
        proc.kill()
        raise RuntimeError(
            f"DevTools не поднялся (chrome жив={alive}, код={proc.returncode}, порт={port}); "
            f"stderr: {tail!r}"
        )

    # Даём странице отработать: скрипт защиты должен выставить свой токен.
    await asyncio.sleep(wait)

    from playwright.async_api import async_playwright

    jar: dict[str, str] = {}
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        if browser.contexts:
            jar = {c["name"]: c["value"] for c in await browser.contexts[0].cookies()}
        # Отключаемся, не закрывая браузер: токен уже выдан.
        await browser.close()

    try:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    shutil.rmtree(profile, ignore_errors=True)
    return jar


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", type=int, default=3)
    ap.add_argument("--warm", type=int, default=0)
    ap.add_argument("--wait", type=float, default=12.0)
    ap.add_argument("--debug-port", type=int, default=9351)
    ap.add_argument("--out", default="var/px-real-chrome.json")
    args = ap.parse_args()

    accounts = json.loads((VAR / "accounts.json").read_text(encoding="utf-8"))
    jar = await harvest("https://www.roblox.com/login", args.wait, port=args.debug_port)
    print("cookie от настоящего Chrome:", sorted(jar))
    print("  _px3:", f"есть, {len(jar.get('_px3', ''))} символов" if "_px3" in jar else "нет")
    if not jar:
        print("cookie не получены — опыт не имеет смысла")
        return 2

    (VAR / "px-real-chrome.json").write_text(
        json.dumps(jar, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    report: dict = {"cookies": sorted(jar), "px3_len": len(jar.get("_px3", "")), "runs": {}}

    warm = accounts[args.warm]
    res = await login(warm["login"], warm["password"], jar)
    report["runs"]["warm"] = {"account": warm["login"], **res}
    print(f"\nконтроль (прогретый {warm['login']}): {res['verdict']} {res['status']}")

    fresh = accounts[args.fresh]
    res2 = await login(fresh["login"], fresh["password"], jar)
    report["runs"]["fresh"] = {"account": fresh["login"], **res2}
    print(f"опыт   (свежий {fresh['login']}):   {res2['verdict']} {res2['status']}")
    print(f"  тело: {res2['body'][:140]}")

    (VAR / "px-real-chrome.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("\n=== вывод ===")
    if res["verdict"] == "SUCCESS" and res2["verdict"] == "GATE":
        print("настоящий токен не помогает свежему аккаунту: решает доверие аккаунта")
    elif res2["verdict"] != "GATE":
        print("настоящий токен снимает гейт — найдена рабочая схема")
    else:
        print("контроль не прошёл: опыт некорректен, нужен другой прогретый аккаунт")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
