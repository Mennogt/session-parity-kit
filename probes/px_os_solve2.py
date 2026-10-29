"""Прохождение проверки в настоящем Chrome: ввод на уровне системы.

Отличия от прежних попыток:
  * браузер — обычное приложение, Playwright подключается только в конце,
    чтобы забрать cookie;
  * окно находится по дескриптору процесса, а не по заголовку;
  * снимок берётся только со своего окна (`PrintWindow`), экран не читается;
  * кнопки ищутся по цвету в пределах окна, нажатие и удержание — системной
    мышью (`SendInput`), то есть для страницы это настоящий ввод.

    python -m probes.px_os_solve --login <login> --password <password> --relay 7891
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import socket
import subprocess
import time
from pathlib import Path

import psutil

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
VAR = Path("var")
ART = Path("fixtures/browser")

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
PW_RENDERFULLCONTENT = 0x00000002


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def browser_hwnd(pid: int) -> int | None:
    """Дескриптор главного окна именно нашего процесса, а не чужого Chrome."""
    WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    found: list[tuple[int, int, wt.RECT]] = []

    def callback(hwnd, _lparam):  # noqa: ANN001
        if not user32.IsWindowVisible(hwnd):
            return True
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        pid_of = int(owner.value)
        if pid_of not in our_pids:
            return True
        rect = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width > 500 and height > 400:
            found.append((hwnd, pid_of, rect))
        return True

    for _ in range(40):
        try:
            proc = psutil.Process(pid)
            our_pids = {pid} | {c.pid for c in proc.children(recursive=True)}
        except psutil.NoSuchProcess:
            return None
        found.clear()
        user32.EnumWindows(WNDENUMPROC(callback), 0)
        if found:
            found.sort(key=lambda x: (x[2].right - x[2].left) * (x[2].bottom - x[2].top),
                       reverse=True)
            return found[0][0]
        time.sleep(0.5)
    return None


def grab(hwnd: int):
    """Снимок только своего окна.

    PrintWindow не отдаёт содержимое при аппаратном ускорении, поэтому если
    картинка вышла одноцветной, берём область окна с экрана (окно поднято
    на передний план, чужие окна в кадр не попадают).
    """
    from PIL import Image

    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return None

    shot = _grab_printwindow(hwnd, width, height)
    if shot is not None and not _is_blank(shot):
        return shot

    user32.SetForegroundWindow(hwnd)
    time.sleep(0.4)
    return _grab_screen(rect.left, rect.top, width, height)


def _grab_printwindow(hwnd: int, width: int, height: int):
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, width, height)
    gdi32.SelectObject(mem, bmp)
    ok = user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                    ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                    ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                    ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                    ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth = width
    bi.biHeight = -height
    bi.biPlanes = 1
    bi.biBitCount = 32
    buffer = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(mem, bmp, 0, height, buffer, ctypes.byref(bi), 0)

    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    if not ok:
        return None
    return width, height, buffer.raw


def _grab_screen(left: int, top: int, width: int, height: int):
    from PIL import ImageGrab

    img = ImageGrab.grab(bbox=(left, top, left + width, top + height))
    return width, height, img.convert("RGB").tobytes()


def _is_blank(shot) -> bool:
    width, height, raw = shot
    sample = []
    step_x = max(1, width // 40)
    step_y = max(1, height // 40)
    for y in range(0, height, step_y):
        base = y * width * 4
        for x in range(0, width, step_x):
            i = base + x * 4
            sample.append((raw[i], raw[i + 1], raw[i + 2]))
    if not sample:
        return True
    colors = {(r // 32, g // 32, b // 32) for r, g, b in sample}
    return len(colors) <= 3


def find_blue(width: int, height: int, raw: bytes) -> tuple[int, int, int] | None:
    """Центр самой крупной синей области в средней части окна."""
    best = None
    best_count = 0
    step = 3
    for y in range(int(height * 0.25), int(height * 0.9), step):
        row = y * width * 4
        for x in range(int(width * 0.15), int(width * 0.85), step):
            i = row + x * 4
            b, g, r = raw[i], raw[i + 1], raw[i + 2]
            if 150 <= b <= 255 and 60 <= g <= 140 and r <= 90:
                count = 0
                for dy in range(0, 40, 4):
                    yy = y + dy
                    if yy >= height:
                        break
                    base = yy * width * 4
                    for dx in range(0, 60, 4):
                        xx = x + dx
                        if xx >= width:
                            break
                        j = base + xx * 4
                        bb, gg, rr = raw[j], raw[j + 1], raw[j + 2]
                        if 150 <= bb <= 255 and 60 <= gg <= 140 and rr <= 90:
                            count += 1
                if count > best_count:
                    best_count = count
                    best = (x + 30, y + 20, count)
    return best


def os_click(x: int, y: int) -> None:
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.1)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.05)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def os_hold(x: int, y: int, seconds: float) -> None:
    import random

    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.3)
    for _ in range(10):
        user32.SetCursorPos(int(x + random.uniform(-1, 1)), int(y + random.uniform(-1, 1)))
        time.sleep(0.02)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        time.sleep(0.1)
        user32.SetCursorPos(int(x + random.uniform(-2, 2)), int(y + random.uniform(-1.5, 1.5)))
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def harvest(port: int, login: str) -> dict:
    import asyncio

    from playwright.async_api import async_playwright

    async def run() -> dict:
        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            jar = {}
            if browser.contexts:
                jar = {c["name"]: c["value"] for c in await browser.contexts[0].cookies()}
            await browser.close()
            return jar

    jar = asyncio.run(run())
    if jar:
        (VAR / f"session-{login}.json").write_text(
            json.dumps(jar, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return jar


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--relay", type=int, default=7891)
    ap.add_argument("--hold", type=float, default=11.0)
    ap.add_argument("--keep-open", action="store_true")
    args = ap.parse_args()

    ART.mkdir(parents=True, exist_ok=True)
    debug_port = free_port()
    profile = (VAR / f"chrome-os-{int(time.time())}").resolve()
    profile.mkdir(parents=True, exist_ok=True)

    proc = subprocess.Popen(
        [CHROME, f"--remote-debugging-port={debug_port}",
         "--remote-debugging-address=127.0.0.1",
         f"--user-data-dir={profile}",
         f"--proxy-server=http://127.0.0.1:{args.relay}",
         "--no-first-run", "--no-default-browser-check",
         "--force-renderer-accessibility",
         "--window-size=1280,860", "--window-position=60,60",
         "https://www.roblox.com/login"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print(f"Chrome запущен, pid {proc.pid}")

    hwnd = browser_hwnd(proc.pid)
    if not hwnd:
        print("окно своего процесса не найдено")
        return 2
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    print(f"окно {hwnd}: ({rect.left},{rect.top})-({rect.right},{rect.bottom})")
    user32.SetForegroundWindow(hwnd)
    # Мобильный прокси медленный: ждём, пока страница реально отрисуется.
    shot = None
    for attempt in range(20):
        time.sleep(2.5)
        shot = grab(hwnd)
        if shot is not None and not _is_blank(shot):
            print(f"страница отрисовалась через {int((attempt + 1) * 2.5)} с")
            break
    if shot is None:
        print("снимок окна не получен")
        return 2
    width, height, raw = shot
    print(f"снимок окна: {width}x{height}")
    Path(ART / "os-window.png").write_bytes(_png(width, height, raw))

    # Поля ввода ищём по раскладке формы: она центрирована и стабильна.
    cx = rect.left + width // 2
    user_y = rect.top + int(height * 0.42)
    pass_y = rect.top + int(height * 0.50)
    os_click(cx, user_y)
    time.sleep(0.3)
    ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)  # Ctrl
    ctypes.windll.user32.keybd_event(0x41, 0, 0, 0)  # A
    ctypes.windll.user32.keybd_event(0x41, 0, 2, 0)
    ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
    time.sleep(0.1)
    _type_text(args.login)
    time.sleep(0.3)
    os_click(cx, pass_y)
    time.sleep(0.2)
    _type_text(args.password)
    print("поля заполнены")

    time.sleep(0.4)
    shot = grab(hwnd)
    if shot:
        found = find_blue(*shot)
        if found:
            bx, by, strength = found
            print(f"синяя кнопка найдена в ({bx},{by}), вес {strength}")
            os_click(rect.left + bx, rect.top + by)
        else:
            print("синяя кнопка не найдена, жмём Enter")
            _press_enter()

    print("ждём проверку...")
    challenge = None
    for _ in range(25):
        time.sleep(1.5)
        shot = grab(hwnd)
        if not shot:
            continue
        found = find_blue(*shot)
        if found and found[2] > 200:
            challenge = found
            break
    if not challenge:
        print("кнопка удержания не появилась")
        Path(ART / "os-no-challenge2.png").write_bytes(_png(*shot)) if shot else None
        return 3

    bx, by, strength = challenge
    print(f"кнопка удержания: ({bx},{by}), вес {strength}; держим {args.hold} с")
    os_hold(rect.left + bx, rect.top + by, args.hold)
    print("отпустили")

    jar = harvest(debug_port, args.login)
    session = jar.get(".ROBLOSECURITY", "")
    print(f"cookie: {sorted(jar)[:8]}; сессия: "
          f"{'есть, длина ' + str(len(session)) if session else 'нет'}")

    result = {"login": args.login, "session_len": len(session), "cookies": sorted(jar)}
    (VAR / "px-os-solve.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    if not args.keep_open:
        try:
            proc.terminate()
        except Exception:
            pass
    return 0


def _type_text(text: str) -> None:
    import ctypes

    for ch in text:
        vk = ctypes.windll.user32.VkKeyScanW(ord(ch))
        code = vk & 0xFF
        shift = (vk >> 8) & 0xFF
        mods = []
        if shift & 1:
            mods.append(0x10)
        if shift & 2:
            mods.append(0x11)
        for m in mods:
            ctypes.windll.user32.keybd_event(m, 0, 0, 0)
        ctypes.windll.user32.keybd_event(code, 0, 0, 0)
        ctypes.windll.user32.keybd_event(code, 0, 2, 0)
        for m in reversed(mods):
            ctypes.windll.user32.keybd_event(m, 0, 2, 0)
        time.sleep(0.015)


def _press_enter() -> None:
    ctypes.windll.user32.keybd_event(0x0D, 0, 0, 0)
    ctypes.windll.user32.keybd_event(0x0D, 0, 2, 0)


def _png(width: int, height: int, raw: bytes) -> bytes:
    from io import BytesIO

    from PIL import Image

    img = Image.frombuffer("RGBA", (width, height), raw, "raw", "BGRA", 0, 1)
    buf = BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


if __name__ == "__main__":
    raise SystemExit(main())
