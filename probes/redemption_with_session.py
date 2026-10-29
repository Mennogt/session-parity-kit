"""Можно ли закрыть challenge свежим redemption-токеном, имея живую сессию.

Схема: вход свежим аккаунтом даёт 403 и `challengeId`. Дальше вызываем
`/v2/captcha` **с приложенной живой cookie** — если платформа считает запрос
доверенным, она может вернуть 200 и `redemptionToken` без интерактивного
испытания. Тогда повторный вход с заголовками `rblx-challenge-*` пройдёт.

    python -m probes.redemption_with_session --account <login>
"""

from __future__ import annotations

import argparse
import base64
import json
import re
from pathlib import Path

import httpx

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
CAPTCHA = "https://apis.roblox.com/v2/captcha?urlLocale=en_us"


def b64json(value: str) -> dict:
    pad = "=" * (-len(value) % 4)
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            return json.loads(decoder(value + pad))
        except Exception:
            continue
    return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="<login>")
    ap.add_argument("--proxy-url", default=None)
    ap.add_argument("--out", default="var/redemption-session.json")
    args = ap.parse_args()

    sessions = json.loads(Path("var/sessions.json").read_text(encoding="utf-8"))
    cookie_entry = next(iter(sessions.values()))
    cookie = cookie_entry["cookie"]
    print(f"живая сессия: {cookie_entry.get('identity') or cookie_entry.get('login')}")

    accounts = json.loads(Path("var/accounts.json").read_text(encoding="utf-8"))
    account = next(a for a in accounts if a["login"].lower() == args.account.lower())
    body = {"ctype": "Username", "cvalue": account["login"], "password": account["password"]}
    report: dict = {"account": account["login"]}

    with httpx.Client(proxy=args.proxy_url, timeout=45, follow_redirects=False,
                      headers=dict(BASE),
                      cookies={".ROBLOSECURITY": cookie}) as client:
        # Токен запроса берём уже в состоянии сессии, иначе он не совпадёт.
        r0 = client.post(LOGIN, json={"ctype": "Username", "cvalue": "probe_zz", "password": "x"})
        token = r0.headers.get("x-csrf-token")
        client.headers["X-CSRF-TOKEN"] = token or ""
        print(f"0) CSRF в сессии: {'есть' if token else 'нет'}, "
              f"cookie в клиенте: {sorted(c.name for c in client.cookies.jar)}")

        r1 = client.post(LOGIN, json=body)
        meta_raw = r1.headers.get("rblx-challenge-metadata")
        ch_id = r1.headers.get("rblx-challenge-id")
        report["login"] = {"status": r1.status_code, "challenge_id": ch_id}
        print(f"1) вход: {r1.status_code}, challenge_id={ch_id}")
        if not meta_raw:
            print(f"   тело: {r1.text[:120]}")
            return 0
        meta = b64json(meta_raw)
        challenge_id = meta.get("challengeId") or ch_id
        print(f"   режим: {meta.get('mode')}")

        # Основной опыт: /v2/captcha в том же клиенте с живой сессией.
        r2 = client.post(CAPTCHA, json={"challenge_id": challenge_id})
        report["captcha_with_session"] = {"status": r2.status_code, "body": r2.text[:1200],
                                          "headers": {k: v for k, v in r2.headers.items()
                                                      if k.startswith("rblx-challenge")}}
        print(f"2) /v2/captcha с сессией: {r2.status_code} {r2.text[:200]!r}")

        tokens = set()
        for source in (r2.text, json.dumps(dict(r2.headers))):
            tokens.update(re.findall(r'"(?:redemptionToken|token|solution)"\s*:\s*"([^"]{16,})"', source))
        print(f"   токенов: {len(tokens)}")
        report["tokens"] = [t[:20] + "…" for t in tokens]

        # Для сравнения: тот же вызов без сессии.
        r3 = client.post(CAPTCHA, json={"challenge_id": challenge_id},
                         headers={**BASE, "X-CSRF-TOKEN": token or ""})
        report["captcha_without_session"] = {"status": r3.status_code, "body": r3.text[:300]}
        print(f"3) /v2/captcha без сессии: {r3.status_code} {r3.text[:120]!r}")

    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== вывод ===")
    if report["captcha_with_session"]["status"] == 200 and tokens:
        print("сессия даёт redemption-токен — вход без капчи возможен")
    elif report["captcha_with_session"]["status"] != report["captcha_without_session"]["status"]:
        print("статусы различаются, но токена нет — нужно смотреть тело")
    else:
        print("сессия не влияет: платформа требует интерактивного испытания")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
