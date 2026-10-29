"""Запуск всего сервиса одной командой.

Поднимает API с панелью и пул воркеров, ждёт готовности и открывает
браузер. Останавливает всё по Ctrl+C.

    python -m tools.launch
    python -m tools.launch --no-browser --workers 4 --port 8080
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent


def wait_ready(url: str, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    env = {**os.environ, "SPK_LOG_JSON": "false", "SPK_WORKER_COUNT": str(args.workers)}
    python = sys.executable

    api = subprocess.Popen(
        [python, "-m", "uvicorn", "api.main:app", "--host", args.host,
         "--port", str(args.port), "--log-level", "warning"],
        cwd=ROOT, env=env,
    )
    url = f"http://{args.host}:{args.port}/"
    if not wait_ready(url + "healthz"):
        print("API не поднялся — смотрите вывод выше", file=sys.stderr)
        api.terminate()
        return 2
    print(f"API готов: {url}")

    worker = subprocess.Popen([python, "-m", "worker.main"], cwd=ROOT, env=env)
    print(f"воркеров запущено: {args.workers}")

    if not args.no_browser:
        webbrowser.open(url)

    def shutdown(*_: object) -> None:
        for process in (worker, api):
            with __import__("contextlib").suppress(Exception):
                process.terminate()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        while True:
            if api.poll() is not None:
                print("API остановился", file=sys.stderr)
                worker.terminate()
                return 1
            if worker.poll() is not None:
                print("воркеры остановились", file=sys.stderr)
                api.terminate()
                return 1
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
