"""Уборка осиротевших браузерных процессов.

Браузеры, запущенные провайдером, всегда живут внутри каталога playwright
(`ms-playwright`). Это точный признак принадлежности: пользовательский
браузер имеет другой путь и никогда не попадает под уборку.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable

from core.logging import get_logger
from obs import metrics

logr = get_logger("procs")

BROWSER_NAMES = {
    "chrome.exe",
    "headless_shell.exe",
    "chromium.exe",
    "firefox.exe",
    "msedge.exe",
    "webkit.exe",
    "node.exe",
}


def _playwright_roots() -> list[str]:
    roots: list[str] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(str(Path(local) / "ms-playwright").lower())
    home = Path.home()
    for candidate in (
        home / ".cache" / "ms-playwright",
        home / "Library" / "Caches" / "ms-playwright",
    ):
        roots.append(str(candidate).lower())
    return roots


def _is_ours(exe: str | None) -> bool:
    if not exe:
        return False
    low = exe.lower()
    return any(root in low for root in _playwright_roots())


def probe_support() -> bool:
    try:
        import psutil  # noqa: F401
    except ImportError:
        return False
    return True


def _ours() -> Iterable["object"]:
    import psutil

    for proc in psutil.process_iter(["pid", "name", "exe"]):
        try:
            info = proc.info
        except Exception:
            continue
        name = (info.get("name") or "").lower()
        if name not in BROWSER_NAMES:
            continue
        if _is_ours(info.get("exe")):
            yield proc


def cleanup(keep_pids: set[int] | None = None) -> int:
    """Убить браузерные процессы playwright, кроме перечисленных. Вернуть число."""
    if not probe_support():
        return 0
    keep = keep_pids or set()
    killed = 0
    for proc in list(_ours()):
        pid = getattr(proc, "pid", None)
        if pid in keep:
            continue
        try:
            children = proc.children(recursive=True)
        except Exception:
            children = []
        for child in children:
            try:
                child.kill()
                killed += 1
            except Exception:
                pass
        try:
            proc.kill()
            killed += 1
        except Exception:
            continue
    if killed:
        metrics.processes_orphaned_total.inc(killed)
        logr.warning("убрано осиротевших процессов: %s", killed)
    return killed


def platform_supported() -> bool:
    return sys.platform in ("win32", "linux", "darwin")
