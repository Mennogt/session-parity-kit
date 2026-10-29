"""Прохождение проверки PerimeterX в настоящем Chrome, без автоматизации.

Чем отличается от прежних попыток:
  * браузер запускается как обычное приложение, Playwright к нему не
    подключается до момента успеха (только чтобы забрать cookie потом);
  * элементы находятся через UI Automation Windows, а не через DOM;
  * нажатие и удержание идут через системный ввод (SendInput), то есть для
    страницы это настоящая мышь, а не событие CDP.

    python -m probes.px_os_solve --login <login> --password <password> --relay 7891
"""

from __future__ import annotations

import argparse
import ctypes
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
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def tree_pids(root_pid: int) -> set[int]:
    out = {root_pid}
    try:
        proc = psutil.Process(root_pid)
        for child in proc.children(recursive=True):
            out.add(child.pid)
    except psutil.Error:
        pass
    return out


def find_window(pids: set[int]):
    import uiautomation as auto

    for _ in range(60):
        for win in auto.GetRootControl().GetChildren():
            try:
                if win.ProcessId in pids and win.ClassName == "Chrome_WidgetWin_1":
                    return win
            except Exception:
                continue
        time.sleep(0.5)
    return None


def os_click(x: int, y: int) -> None:
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.12)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.06)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def os_hold(x: int, y: int, seconds: float) -> None:
    """Удержание системной мышью с небольшим дрейфом."""
    import random

    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.25)
    # Подводим курсор несколькими шагами, как живая рука.
    for step in range(12):
        user32.SetCursorPos(int(x + random.uniform(-1, 1)), int(y + random.uniform(-1, 1)))
        time.sleep(0.02)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        time.sleep(0.11)
        user32.SetCursorPos(int(x + random.uniform(-2.0, 2.0)), int(y + random.uniform(-1.5, 1.5)))
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def find_edit_controls(win) -> list:
    import uiautomation as auto

    edits = []
    for ctrl in win.GetChildren():
        stack = [ctrl]
        while stack:
            node = stack.pop()
            try:
                if node.ControlTypeName == "EditControl":
                    edits.append(node)
                stack.extend(node.GetChildren())
            except Exception:
                continue
    edits.sort(key=lambda c: c.BoundingRectangle.top)
    return edits


def dump_tree(win, path: Path, depth: int = 12) -> None:
    """Сохранить дерево элементов: по нему видно, что доступно автоматизации."""
    lines: list[str] = []

    def walk(node, level: int) -> None:
        if level > depth or len(lines) > 400:
            return
        try:
            rect = node.BoundingRectangle
            lines.append(
                f"{'  ' * level}{node.ControlTypeName} name={node.Name!r} "
                f"class={node.ClassName!r} rect=({rect.left},{rect.top},{rect.right},{rect.bottom})"
            )
            for child in node.GetChildren():
                walk(child, level + 1)
        except Exception:
            return

    walk(win, 0)
    path.write_text("\n".join(lines), encoding="utf-8")


def find_button(win, names: tuple[str, ...]):
    stack = [win]
    while stack:
        node = stack.pop()
        try:
            if node.ControlTypeName == "ButtonControl":
                name = (node.Name or "").strip()
                low = name.lower()
                if any(n in low for n in names):
                    return node
            stack.extend(node.GetChildren())
        except Exception:
            continue
    return None


def harvest_cookies(port: int, login: str) -> dict:
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
    ap.add_argument("--relay", type=int, default=7891, help="порт локального релея к прокси")
    ap.add_argument("--hold", type=float, default=11.0)
    ap.add_argument("--keep-open", action="store_true")
    ap.add_argument("--out", default="var/px-os-solve.json")
    args = ap.parse_args()

    import uiautomation as auto

    ART.mkdir(parents=True, exist_ok=True)
    debug_port = free_port()
    profile = (VAR / f"chrome-os-{int(time.time())}").resolve()
    profile.mkdir(parents=True, exist_ok=True)

    proc = subprocess.Popen(
        [
            CHROME,
            f"--remote-debugging-port={debug_port}",
            "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={profile}",
            f"--proxy-server=http://127.0.0.1:{args.relay}",
            "--no-first-run",
            "--no-default-browser-check",
            "--force-renderer-accessibility",
            "--window-size=1280,860",
            "--window-position=40,40",
            "https://www.roblox.com/login",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(f"Chrome запущен, pid {proc.pid}, окно готовится...")

    pids = tree_pids(proc.pid)
    time.sleep(4)
    pids |= tree_pids(proc.pid)
    win = find_window(pids)
    if win is None:
        print("окно Chrome не найдено")
        return 2
    win.SetActive()
    win.SetFocus()
    time.sleep(3)
    print(f"окно найдено: {win.Name!r}")

    edits = find_edit_controls(win)
    print(f"полей ввода найдено: {len(edits)}")
    if len(edits) < 2:
        win.CaptureToImage(str(ART / "os-no-fields.png"))
        dump_tree(win, ART / "os-tree.txt")
        print("не нашёл два поля — снимок и дерево сохранены")
        return 2

    user_field, pass_field = edits[0], edits[1]
    for field, value, label in ((user_field, args.login, "логин"),
                                (pass_field, args.password, "пароль")):
        rect = field.BoundingRectangle
        os_click((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
        time.sleep(0.2)
        auto.SendKeys("{Ctrl}a", waitTime=0.05)
        auto.SendKeys(value, waitTime=0.02)
        print(f"  введён {label}")

    time.sleep(0.3)
    button = find_button(win, ("log in", "войти", "login"))
    if button is None:
        print("кнопка входа не найдена")
        return 2
    rect = button.BoundingRectangle
    os_click((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
    print("форма отправлена, ждём проверку...")

    deadline = time.monotonic() + 60
    hold_button = None
    while time.monotonic() < deadline:
        time.sleep(1.5)
        hold_button = find_button(win, ("hold", "удерж", "нажмите"))
        if hold_button is not None:
            break
    if hold_button is None:
        win.CaptureToImage(str(ART / "os-no-challenge.png"))
        print("кнопка удержания не появилась — снимок сохранён")
        return 3

    rect = hold_button.BoundingRectangle
    cx, cy = (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
    print(f"кнопка удержания: {hold_button.Name!r} в ({cx},{cy}); держим {args.hold} с")
    os_hold(cx, cy, args.hold)
    print("отпустили, ждём результат...")

    outcome = "неизвестно"
    for tick in range(90):
        time.sleep(1)
        title = win.Name or ""
        challenge = find_button(win, ("hold", "удерж", "нажмите"))
        if challenge is not None and tick > 3:
            outcome = "проверка показана снова"
            break
        if "roblox" in title.lower() and challenge is None:
            outcome = "проверка ушла"
            break
    win.CaptureToImage(str(ART / "os-after-hold.png"))

    jar = harvest_cookies(debug_port, args.login)
    session = jar.get(".ROBLOSECURITY", "")
    print(f"итог: {outcome}; cookie в браузере: {sorted(jar)[:8]}")
    print(f"сессия: {'есть, длина ' + str(len(session)) if session else 'нет'}")

    report = {"login": args.login, "outcome": outcome,
              "session_len": len(session), "cookies": sorted(jar)}
    (VAR / args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.keep_open:
        try:
            proc.terminate()
        except Exception:
            pass
    else:
        print("окно оставлено открытым")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
