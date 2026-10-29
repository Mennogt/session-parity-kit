"""Вторая волна проб: условия, при которых гейт может сниматься.

Проверяем не маршруты, а обстоятельства:
  * есть ли уже сессия (пусть чужого аккаунта) — иногда снимает гейт;
  * HTTP/1.1 против HTTP/2;
  * полный набор браузерных заголовков, включая priority/sec-ch-ua;
  * параметр urlLocale, который добавляет сам сайт;
  * заголовки приложения Roblox;
  * пустые/заполненные поля captcha*.

Пароль везде неверный: успешного входа не происходит.

    python -m probes.gate_probe --account 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import httpx

PROBE_PASSWORD = "gate_probe_wrong_password"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
URL = "https://auth.roblox.com/v2/login"

FULL_CHROME = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://www.roblox.com",
    "Referer": "https://www.roblox.com/login",
    "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "priority": "u=1, i",
}


def verdict_of(status: int, body: str) -> str:
    low = body.lower()
    if "token validation failed" in low:
        return "csrf-only"
    if "challenge is required" in low:
        return "GATE"
    if "incorrect username or password" in low:
        return "NO GATE"
    return f"other:{status}"


async def probe(name: str, *, headers: dict | None = None, json_body: dict | None = None,
                cookies: dict | None = None, http2: bool = True, url: str = URL,
                method: str = "POST", data: dict | None = None) -> dict:
    h = dict(FULL_CHROME)
    if headers:
        h.update(headers)
    body = json_body if json_body is not None else {
        "ctype": "Username", "cvalue": "", "password": PROBE_PASSWORD
    }
    try:
        async with httpx.AsyncClient(
            timeout=30, follow_redirects=False, http2=http2, cookies=cookies or {}
        ) as client:
            kwargs: dict = {"headers": h}
            if data is not None:
                kwargs["data"] = data
            else:
                kwargs["json"] = body
            r = await client.request(method, url, **kwargs)
            token = r.headers.get("x-csrf-token")
            if token:
                h2 = dict(h)
                h2["X-CSRF-TOKEN"] = token
                kwargs["headers"] = h2
                r = await client.request(method, url, **kwargs)
            return {"name": name, "status": r.status_code, "verdict": verdict_of(r.status_code, r.text),
                    "body": r.text[:200],
                    "session_cookie": any(c.name == ".ROBLOSECURITY" for c in client.cookies.jar)}
    except Exception as exc:
        return {"name": name, "error": f"{type(exc).__name__}: {str(exc)[:100]}"}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, default=3)
    ap.add_argument("--session-file", default=None, help="cookie-банка другого аккаунта")
    ap.add_argument("--out", default="var/gate-probe.json")
    args = ap.parse_args()

    accounts = json.loads(Path("var/accounts.json").read_text(encoding="utf-8"))
    login = accounts[args.account]["login"]
    base = {"ctype": "Username", "cvalue": login, "password": PROBE_PASSWORD}

    results: list[dict] = []

    async def run(**kw) -> None:
        kw.setdefault("json_body", base)
        row = await probe(**kw)
        results.append(row)
        print(f"  {row.get('verdict', 'ERROR'):<12} {row['name']:<34} "
              f"{row.get('status', '-')} {row.get('body', row.get('error', ''))[:60]!r}")

    await run(name="базовый (полные заголовки)")
    await run(name="HTTP/1.1", http2=False)
    await run(name="urlLocale=en_us", url=f"{URL}?urlLocale=en_us")
    await run(name="заголовки приложения", headers={"User-Agent": "Roblox/WinInet",
                                                    "Origin": None})
    await run(name="X-Roblox-Id", headers={"X-Roblox-Id": "1"})
    await run(name="Roblox-Id", headers={"Roblox-Id": "1"})
    await run(name="без sec-fetch", headers={"sec-fetch-dest": None, "sec-fetch-mode": None,
                                             "sec-fetch-site": None, "priority": None})
    await run(name="form-encoded", data={"ctype": "Username", "cvalue": login,
                                        "password": PROBE_PASSWORD},
              headers={"Content-Type": "application/x-www-form-urlencoded"})
    await run(name="пустой captchaProvider", json_body={**base, "captchaProvider": ""})
    await run(name="captchaProvider ARKOSE", json_body={
        **base, "captchaProvider": "PROVIDER_ARKOSE_LABS", "captchaToken": "x",
        "captchaId": "x"})

    if args.session_file:
        jar = json.loads(Path(args.session_file).read_text(encoding="utf-8"))
        await run(name="с сессией другого аккаунта", cookies=jar)

    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    free = [r for r in results if r.get("verdict") == "NO GATE"]
    print("\n=== без гейта ===")
    for r in free:
        print(f"  {r['name']} -> {r['body'][:120]}")
    if not free:
        print("  нет ни одного варианта")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
