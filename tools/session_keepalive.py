"""Поддержание банка сессий: проверка, отчёт, продление жизни.

Каждая живая сессия продлевается самим фактом использования: Roblox
обновляет срок действия при авторизованных запросах. Скрипт проходит по
банку, дёргает контрольный эндпоинт и помечает мёртвые.

    python -m tools.session_keepalive --interval 3600
    python -m tools.session_keepalive --once
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx

BANK = Path("var/sessions.json")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept": "application/json",
           "Origin": "https://www.roblox.com", "Referer": "https://www.roblox.com/home"}


def load() -> dict:
    return json.loads(BANK.read_text(encoding="utf-8")) if BANK.exists() else {}


def save(data: dict) -> None:
    BANK.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def check(cookie: str) -> dict:
    try:
        with httpx.Client(timeout=30, follow_redirects=False,
                          cookies={".ROBLOSECURITY": cookie}) as client:
            r = client.get("https://users.roblox.com/v1/users/authenticated", headers=HEADERS)
        data = {}
        try:
            data = r.json()
        except Exception:
            pass
        return {"ok": r.status_code == 200, "status": r.status_code,
                "name": data.get("name"), "body": r.text[:160]}
    except Exception as exc:
        return {"ok": False, "status": None, "error": f"{type(exc).__name__}: {str(exc)[:100]}"}


def sweep(data: dict) -> tuple[int, int]:
    alive = dead = 0
    for entry in data.values():
        result = check(entry["cookie"])
        entry["ok"] = result["ok"]
        entry["verified_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        if result["ok"]:
            alive += 1
            entry["identity"] = result.get("name") or entry.get("identity")
        else:
            dead += 1
            entry["last_error"] = result.get("body") or result.get("error")
            print(f"  мёртвая: {entry['login']} -> {result.get('status')} "
                  f"{(result.get('body') or result.get('error') or '')[:80]}")
    return alive, dead


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=float, default=3600.0)
    args = ap.parse_args()

    while True:
        data = load()
        if not data:
            print("банк пуст")
            return 2
        started = time.strftime("%H:%M:%S")
        alive, dead = sweep(data)
        save(data)
        print(f"{started} проверено {len(data)}: живых {alive}, мёртвых {dead}", flush=True)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
