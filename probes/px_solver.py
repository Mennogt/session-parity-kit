"""Собственный решатель PerimeterX: геометрия через DOM, нажатие — системное.

Идея. Прошлые попытки проваливались потому, что удержание отправлялось
событиями CDP (`Input.dispatchMouseEvent`) — это не настоящий ввод. Здесь
разделено:

  * Playwright/CDP используется только чтобы заполнить форму и узнать
    координаты кнопки удержания в системе координат экрана;
  * затем соединение CDP закрывается, и удержание выполняется системной
    мышью через `SendInput` — для страницы это живая рука;
  * после отпускания CDP подключается заново и читает cookie.

    python -u -m probes.px_solver --login <login> --password <password>
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import ctypes.wintypes as wt
import json
import random
import socket
import subprocess
import time
from pathlib import Path

import psutil

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
VAR = Path("var")
user32 = ctypes.windll.user32
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def find_hwnd(pid: int) -> int | None:
    WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    best: list[tuple[int, int]] = []

    def callback(hwnd, _):  # noqa: ANN001
        if not user32.IsWindowVisible(hwnd):
            return True
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if int(owner.value) != pid:
            return True
        rect = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        area = (rect.right - rect.left) * (rect.bottom - rect.top)
        if area > 300_000:
            best.append((area, hwnd))
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    if not best:
        return None
    best.sort(reverse=True)
    return best[0][1]


def hold_at(x: int, y: int, seconds: float) -> None:
    """Системное нажатие с удержанием и микродвижением."""
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.35)
    for _ in range(14):
        user32.SetCursorPos(int(x + random.uniform(-1.5, 1.5)),
                            int(y + random.uniform(-1.5, 1.5)))
        time.sleep(0.03)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        time.sleep(0.12)
        user32.SetCursorPos(int(x + random.uniform(-2.0, 2.0)),
                            int(y + random.uniform(-1.5, 1.5)))
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


HOLD_SELECTORS = [
    "#px-captcha",
    "div[role='button']",
    "button",
]


async def run(login: str, password: str, relay: int, hold: float, keep: bool) -> dict:
    from playwright.async_api import async_playwright

    result: dict = {"login": login}
    debug_port = free_port()
    profile = (VAR / f"chrome-px-{int(time.time())}").resolve()
    profile.mkdir(parents=True, exist_ok=True)

    proc = subprocess.Popen(
        [CHROME, f"--remote-debugging-port={debug_port}",
         "--remote-debugging-address=127.0.0.1",
         f"--user-data-dir={profile}",
         f"--proxy-server=http://127.0.0.1:{relay}",
         "--no-first-run", "--no-default-browser-check",
         "--window-size=1280,900", "--window-position=60,40",
         "https://o0.roblox.com/login"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print(f"Chrome pid {proc.pid}, порт {debug_port}", flush=True)

    hwnd = None
    for _ in range(40):
        time.sleep(0.5)
        hwnd = find_hwnd(proc.pid)
        if hwnd:
            break
    if not hwnd:
        print("окно не найдено")
        return result
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    print(f"окно ({rect.left},{rect.top})-({rect.right},{rect.bottom})", flush=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{debug_port}")
        context = browser.contexts[0]
        page = context.pages[0]
        await page.goto("https://www.roblox.com/login", wait_until="domcontentloaded")
        await page.wait_for_timeout(4000)

        user_box = await page.query_selector("input[name='username']")
        pass_box = await page.query_selector("input[name='password']")
        if not user_box or not pass_box:
            print("поля не найдены")
            return result
        await user_box.fill(login)
        await pass_box.fill(password)
        await page.keyboard.press("Enter")
        print("форма отправлена, ждём проверку", flush=True)

        target = None
        for _ in range(60):
            await page.wait_for_timeout(1000)
            for frame in page.frames:
                url = frame.url or ""
                if "perimeterx" in url or "px-cdn" in url or "captcha" in url:
                    try:
                        box = await frame.evaluate(
                            """() => {
                                const cands = Array.from(document.querySelectorAll(
                                  '#px-captcha, div[role=button], button, [class*=button]'));
                                const vis = cands
                                  .map(el => ({el, r: el.getBoundingClientRect()}))
                                  .filter(o => o.r.width > 60 && o.r.height > 30);
                                if (!vis.length) return null;
                                vis.sort((a, b) => b.r.width * b.r.height - a.r.width * a.r.height);
                                const r = vis[0].r;
                                return {x: r.x + r.width / 2, y: r.y + r.height / 2,
                                        w: r.width, h: r.height, url: location.href};
                            }"""
                        )
                    except Exception:
                        box = None
                    if box:
                        target = (frame, box)
                        break
            if target:
                break
            if await page.query_selector("text=Home"):
                result["status"] = "без проверки"
                break

        if not target:
            print("кнопка удержания не найдена — снимаю состояние", flush=True)
            Path("var/px").mkdir(parents=True, exist_ok=True)
            try:
                await page.screenshot(path="var/px/shot.png", full_page=False)
                print("снимок страницы сохранён", flush=True)
            except Exception as exc:
                print("снимок не вышел:", type(exc).__name__, flush=True)
            print("адрес:", page.url, flush=True)
            for i, frame in enumerate(page.frames):
                print(f"  фрейм {i}: {frame.url[:110]}", flush=True)
            try:
                cands = await page.evaluate(
                    """() => {
                        const out = [];
                        document.querySelectorAll('*').forEach(el => {
                            const id = el.id || '', cls = String(el.className || '');
                            const txt = (el.innerText || '').trim().slice(0, 40);
                            if (!/px-|captcha|hold|verify/i.test(id + ' ' + cls + ' ' + txt)) return;
                            const r = el.getBoundingClientRect();
                            if (r.width < 40 || r.height < 20) return;
                            out.push({tag: el.tagName, id, cls: cls.slice(0, 60),
                                      txt, x: Math.round(r.x + r.width / 2),
                                      y: Math.round(r.y + r.height / 2),
                                      w: Math.round(r.width), h: Math.round(r.height)});
                        });
                        return out.slice(0, 25);
                    }"""
                )
                for c in cands:
                    print("   кандидат:", json.dumps(c, ensure_ascii=False), flush=True)
            except Exception as exc:
                print("разбор не вышел:", type(exc).__name__, flush=True)
            result["status"] = "нет кнопки"
        else:
            frame, box = target
            print(f"кнопка: {box}", flush=True)
            # Смещение области страницы внутри окна.
            metrics = await page.evaluate(
                """() => ({ow: window.outerWidth, iw: window.innerWidth,
                            oh: window.outerHeight, ih: window.innerHeight,
                            sx: window.screenX, sy: window.screenY,
                            dpr: window.devicePixelRatio})"""
            )
            print(f"окружение: {metrics}", flush=True)
            zoom_x = metrics["ow"] - metrics["iw"]
            zoom_y = metrics["oh"] - metrics["ih"]
            screen_x = metrics["sx"] + zoom_x / 2 + box["x"] * metrics["dpr"]
            screen_y = metrics["sy"] + zoom_y + box["y"] * metrics["dpr"]
            print(f"цель на экране: ({screen_x:.0f},{screen_y:.0f}), держим {hold} с",
                  flush=True)

            user32.SetForegroundWindow(hwnd)
            time.sleep(0.5)
            hold_at(int(screen_x), int(screen_y), hold)
            print("отпустили", flush=True)
            await page.wait_for_timeout(6000)
            result["status"] = "удержание выполнено"

        cookies = {c["name"]: c["value"] for c in await context.cookies()}
        result["cookies"] = sorted(cookies)
        result["px3"] = len(cookies.get("_px3", ""))
        result["session"] = len(cookies.get(".ROBLOSECURITY", ""))
        if cookies.get(".ROBLOSECURITY"):
            (VAR / f"session-{login}.json").write_text(
                json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"итог: {result['status']}; _px3={result['px3']}, "
              f"сессия={result['session']}", flush=True)
        await browser.close()

    if not keep:
        try:
            proc.terminate()
        except Exception:
            pass
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--relay", type=int, default=7891)
    ap.add_argument("--hold", type=float, default=12.0)
    ap.add_argument("--keep-open", action="store_true")
    args = ap.parse_args()
    res = asyncio.run(run(args.login, args.password, args.relay, args.hold,
                         args.keep_open))
    Path("var/px-solver.json").write_text(json.dumps(res, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
