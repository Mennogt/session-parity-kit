"""Проверка cookie-сессий по нескольким каналам.

Для каждой `.ROBLOSECURITY` проверяем:
  1. напрямую (домашний адрес);
  2. через мобильный прокси;
  3. через старый помеченный пул;
  4. пару авторизованных эндпоинтов, чтобы понять, какая сессия что может.

    python -m probes.cookie_matrix --file var/cookies-raw.txt
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json",
    "Origin": "https://www.roblox.com",
    "Referer": "https://www.roblox.com/home",
}
VERIFY = "https://users.roblox.com/v1/users/authenticated"
EXTRA = [
    ("mobileapi/userinfo", "https://www.roblox.com/mobileapi/userinfo"),
    ("friends", "https://friends.roblox.com/v1/my/friends/count"),
    ("premium", "https://premiumfeatures.roblox.com/v1/users/{uid}/validate-membership"),
]


def read_cookies(path: Path) -> list[str]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(".ROBLOSECURITY="):
            line = line.split("=", 1)[1]
        out.append(line)
    return out


def call(url: str, cookie: str, proxy: str | None, timeout: float = 30.0) -> dict:
    started = time.perf_counter()
    try:
        with httpx.Client(proxy=proxy, timeout=timeout, follow_redirects=False,
                          cookies={".ROBLOSECURITY": cookie}) as client:
            r = client.get(url, headers=HEADERS)
        body = r.text[:200]
        try:
            data = r.json()
        except Exception:
            data = {}
        return {"status": r.status_code, "ok": r.status_code == 200, "body": body,
                "data": data, "ms": int((time.perf_counter() - started) * 1000)}
    except Exception as exc:
        return {"status": None, "ok": False, "error": f"{type(exc).__name__}: {str(exc)[:90]}",
                "ms": int((time.perf_counter() - started) * 1000)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="var/cookies-raw.txt")
    ap.add_argument("--mobile-urls", default="var/mobile-urls.txt")
    ap.add_argument("--old-url", default=None, help="URL старого пула для проверки")
    ap.add_argument("--out", default="var/cookie-matrix.json")
    args = ap.parse_args()

    cookies = read_cookies(Path(args.file))
    mobiles = [u for u in Path(args.mobile_urls).read_text(encoding="utf-8").splitlines() if u.strip()]
    old_url = args.old_url
    if old_url is None:
        try:
            from probes.proxy_check import proxy_url

            old_url = proxy_url(10110)
        except Exception:
            old_url = None

    print(f"cookie в файле: {len(cookies)}")
    report: list[dict] = []

    for i, cookie in enumerate(cookies):
        row: dict = {"index": i + 1, "len": len(cookie), "checks": {}}
        print(f"\n--- cookie #{i + 1} (длина {len(cookie)}) ---")

        direct = call(VERIFY, cookie, None)
        row["checks"]["direct"] = direct
        ident = direct.get("data") or {}
        uid = ident.get("id")
        row["identity"] = {"id": uid, "name": ident.get("name"), "display": ident.get("displayName")}
        print(f"  напрямую:        {direct['status']} {row['identity']['name'] or direct.get('body', '')[:80]}")

        if mobiles:
            mobile = call(VERIFY, cookie, mobiles[i % len(mobiles)])
            row["checks"]["mobile"] = mobile
            print(f"  мобильный прокси:{mobile['status']} "
                  f"{(mobile.get('data') or {}).get('name') or mobile.get('body', '')[:80]}")

        if old_url:
            old = call(VERIFY, cookie, old_url)
            row["checks"]["old_proxy"] = old
            print(f"  старый пул:      {old['status']} {old.get('body', '')[:80]}")

        if uid:
            for label, template in EXTRA:
                url = template.format(uid=uid)
                res = call(url, cookie, None)
                row["checks"][label] = res
                print(f"  {label:<18} {res['status']} {str(res.get('data') or res.get('body', ''))[:70]}")

        alive = [k for k, v in row["checks"].items() if v.get("ok")]
        row["alive_channels"] = alive
        report.append(row)

    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== итог ===")
    for row in report:
        print(f"  #{row['index']} {row.get('identity', {}).get('name') or '?':<20} "
              f"каналы: {', '.join(row['alive_channels']) or 'нет'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
