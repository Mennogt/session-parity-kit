"""Банк сессий: приём cookie `.ROBLOSECURITY`, проверка, хранение, отдача.

Зачем: проверка стоит только на входе по паролю. Валидная сессия работает
без входа, значит и без проверки. Банк отделяет «получить сессию» (человек)
от «использовать сессию» (сервис).

    python -m tools.session_bank add --login MyAcc --cookie "<значение>"
    python -m tools.session_bank add --login MyAcc --file var/cookie.txt
    python -m tools.session_bank verify --login MyAcc
    python -m tools.session_bank verify --all
    python -m tools.session_bank list
    python -m tools.session_bank get --login MyAcc

Проверка идёт контрольным запросом к `users.roblox.com/v1/users/authenticated`
напрямую и, если указан `--proxy`, через прокси — чтобы сразу увидеть,
переносится ли сессия на другой адрес.
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
VERIFY = "https://users.roblox.com/v1/users/authenticated"


def load() -> dict:
    if not BANK.exists():
        return {}
    return json.loads(BANK.read_text(encoding="utf-8"))


def save(data: dict) -> None:
    BANK.parent.mkdir(parents=True, exist_ok=True)
    BANK.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_cookie(args: argparse.Namespace) -> str:
    if args.cookie:
        value = args.cookie.strip()
    elif args.file:
        value = Path(args.file).read_text(encoding="utf-8").strip()
    else:
        raise SystemExit("нужен --cookie или --file")
    # Принимаем и чистый токен, и строку вида ".ROBLOSECURITY=..."
    if value.startswith(".ROBLOSECURITY="):
        value = value.split("=", 1)[1]
    # Файл Netscape: берём последнюю колонку нужной строки.
    if "\t" in value:
        for line in value.splitlines():
            parts = line.split("\t")
            if len(parts) >= 7 and parts[5] == ".ROBLOSECURITY":
                return parts[6].strip()
        raise SystemExit("в файле нет строки .ROBLOSECURITY")
    if "\n" in value:
        raise SystemExit("значение cookie содержит перевод строки — проверь файл")
    return value


async def verify_one(cookie: str, proxy: str | None) -> dict:
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            proxy=proxy, timeout=httpx.Timeout(connect=20, read=30, write=30, pool=20),
            follow_redirects=False, cookies={".ROBLOSECURITY": cookie},
        ) as client:
            resp = await client.get(
                VERIFY,
                headers={"User-Agent": UA, "Accept": "application/json",
                         "Origin": "https://www.roblox.com",
                         "Referer": "https://www.roblox.com/home"},
            )
        body = resp.text[:300]
        try:
            data = resp.json()
        except Exception:
            data = {}
        return {
            "ok": resp.status_code == 200,
            "status": resp.status_code,
            "identity": data.get("name") or data.get("id"),
            "body": body,
            "ms": int((time.perf_counter() - started) * 1000),
            "proxy": bool(proxy),
        }
    except Exception as exc:
        return {"ok": False, "status": None, "error": f"{type(exc).__name__}: {str(exc)[:120]}",
                "ms": int((time.perf_counter() - started) * 1000), "proxy": bool(proxy)}


def main() -> int:
    import asyncio

    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    add = sub.add_parser("add", help="добавить cookie")
    add.add_argument("--login", required=True)
    add.add_argument("--cookie")
    add.add_argument("--file")
    add.add_argument("--proxy", default=None)
    add.add_argument("--no-verify", action="store_true")

    imp = sub.add_parser("import", help="импортировать файл: по одной cookie в строке")
    imp.add_argument("--file", required=True)
    imp.add_argument("--proxy", default=None)

    ver = sub.add_parser("verify", help="проверить сессии")
    ver.add_argument("--login")
    ver.add_argument("--all", action="store_true")
    ver.add_argument("--proxy", default=None)

    lst = sub.add_parser("list", help="список сессий")
    get = sub.add_parser("get", help="значение cookie")
    get.add_argument("--login", required=True)

    args = ap.parse_args()
    data = load()

    if args.cmd == "add":
        cookie = read_cookie(args)
        entry = {
            "login": args.login,
            "cookie": cookie,
            "len": len(cookie),
            "added_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "verified_at": None,
            "ok": None,
            "identity": None,
        }
        if not args.no_verify:
            result = asyncio.run(verify_one(cookie, args.proxy))
            entry.update({"ok": result["ok"], "identity": result.get("identity"),
                          "verified_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
            print(f"проверка: {'живая' if result['ok'] else 'не прошла'} "
                  f"({result.get('status')}) {result.get('identity') or result.get('body', '')[:120]}")
        data[args.login.lower()] = entry
        save(data)
        print(f"добавлено: {args.login}, длина cookie {len(cookie)}; всего в банке: {len(data)}")
        return 0

    if args.cmd == "import":
        lines = [ln.strip() for ln in Path(args.file).read_text(encoding="utf-8").splitlines()
                 if ln.strip() and not ln.strip().startswith("#")]
        added = 0
        for line in lines:
            if line.startswith(".ROBLOSECURITY="):
                line = line.split("=", 1)[1]
            result = asyncio.run(verify_one(line, args.proxy))
            if not result.get("identity"):
                print(f"  пропуск: {result.get('status')} {result.get('body', '')[:80]}")
                continue
            login = str(result["identity"])
            data[login.lower()] = {
                "login": login,
                "cookie": line,
                "len": len(line),
                "added_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "verified_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "ok": True,
                "identity": login,
            }
            added += 1
            print(f"  добавлено: {login} (длина {len(line)})")
        save(data)
        print(f"импортировано: {added}; всего в банке: {len(data)}")
        return 0

    if args.cmd == "verify":
        targets = [args.login.lower()] if args.login else list(data)
        if not targets:
            print("банк пуст")
            return 2
        for login in targets:
            entry = data.get(login)
            if not entry:
                print(f"{login}: нет в банке")
                continue
            result = asyncio.run(verify_one(entry["cookie"], args.proxy))
            entry.update({"ok": result["ok"], "identity": result.get("identity"),
                          "verified_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
            mark = "живая" if result["ok"] else "мертва"
            print(f"{entry['login']:<22} {mark:<8} {result.get('status')} "
                  f"{result.get('identity') or result.get('body', result.get('error', ''))[:80]}")
        save(data)
        return 0

    if args.cmd == "list":
        if not data:
            print("банк пуст")
            return 0
        for entry in data.values():
            state = "живая" if entry.get("ok") else ("мертва" if entry.get("ok") is False else "не проверена")
            print(f"{entry['login']:<22} {state:<12} добавлена {entry['added_at']} "
                  f"проверена {entry.get('verified_at') or '-'}")
        return 0

    if args.cmd == "get":
        entry = data.get(args.login.lower())
        if not entry:
            print("нет в банке")
            return 2
        print(entry["cookie"])
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
