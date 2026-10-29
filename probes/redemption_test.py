"""Последнее звено протокола: выдаёт ли платформа redemption-токен.

Полный путь клиента:
  1. `POST /v2/login` → 403 + `rblx-challenge-metadata` (base64 JSON).
  2. `POST apis.roblox.com/v2/captcha {"challenge_id"}` → параметры PerimeterX.
  3. После прохождения проверки тот же вызов отдаёт `redemptionToken`.
  4. Повторный `POST /v2/login` с заголовками `rblx-challenge-*`, где в
     метаданных уже заполнен `redemptionToken`.

Проверяем шаг 2 с настоящей cookie `_px3`, снятой чистым Chrome: если
платформа верит cookie, токен придёт без интерактивного решения.

    python -m probes.redemption_test --account 3
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import re
from pathlib import Path

import httpx

from probes.m1_browser import UA
from probes.px_real_chrome import harvest

VAR = Path("var")
BASE = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://www.roblox.com",
    "Referer": "https://www.roblox.com/login",
    "Content-Type": "application/json;charset=UTF-8",
}


def b64json(value: str) -> dict | None:
    pad = "=" * (-len(value) % 4)
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            return json.loads(decoder(value + pad))
        except Exception:
            continue
    return None


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, default=3)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--wait", type=float, default=12.0)
    ap.add_argument("--out", default="redemption-result.json")
    args = ap.parse_args()

    accounts = json.loads((VAR / "accounts.json").read_text(encoding="utf-8"))
    account = accounts[args.account]

    print("снимаю cookie чистым Chrome...")
    jar = await harvest("https://www.roblox.com/login", args.wait, port=9361)
    print(f"  cookie: {sorted(jar)}; _px3={len(jar.get('_px3',''))} символов")

    proxy = None
    if args.port:
        from probes.proxy_check import proxy_url

        proxy = proxy_url(args.port)

    result: dict = {"cookies": sorted(jar), "px3_len": len(jar.get("_px3", ""))}

    async with httpx.AsyncClient(proxy=proxy, timeout=40, follow_redirects=False,
                                 headers=dict(BASE), cookies=jar) as client:
        r0 = await client.post("https://auth.roblox.com/v2/login",
                               json={"ctype": "Username", "cvalue": "probe_zz_none", "password": "x"})
        token = r0.headers.get("x-csrf-token")
        if token:
            client.headers["X-CSRF-TOKEN"] = token

        body = {"ctype": "Username", "cvalue": account["login"], "password": account["password"]}
        r1 = await client.post("https://auth.roblox.com/v2/login", json=body)
        meta_raw = r1.headers.get("rblx-challenge-metadata")
        result["login_status"] = r1.status_code
        print(f"обмен: {r1.status_code}, метаданные {'есть' if meta_raw else 'нет'}")
        if not meta_raw:
            print(f"  тело: {r1.text[:160]}")
            (VAR / args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
            return 0

        meta = b64json(meta_raw) or {}
        cid = meta.get("challengeId")
        print(f"challenge_id: {cid}; режим: {meta.get('mode')}")

        r2 = await client.post(
            "https://apis.roblox.com/v2/captcha?urlLocale=en_us",
            json={"challenge_id": cid},
            headers={**BASE, "X-CSRF-TOKEN": client.headers.get("X-CSRF-TOKEN", "")},
        )
        result["captcha_status"] = r2.status_code
        result["captcha_body"] = r2.text[:1500]
        print(f"/v2/captcha: {r2.status_code}")

        tokens = set()
        for source in (r2.text, json.dumps({k: v for k, v in r2.headers.items()})):
            tokens.update(re.findall(r'"(?:redemptionToken|token|solution)"\s*:\s*"([^"]{16,})"', source))
        result["tokens_found"] = [t[:24] + "…" for t in tokens]
        print(f"  токены в ответе: {len(tokens)}")

        if tokens:
            tok = tokens.pop()
            patched = dict(meta)
            patched["redemptionToken"] = tok
            payload = base64.b64encode(json.dumps(patched).encode()).decode()
            headers = dict(BASE)
            headers["rblx-challenge-id"] = str(cid)
            headers["rblx-challenge-metadata"] = payload
            headers["X-CSRF-TOKEN"] = client.headers.get("X-CSRF-TOKEN", "")
            r3 = await client.post("https://auth.roblox.com/v2/login", json=body, headers=headers)
            result["retry_status"] = r3.status_code
            result["retry_body"] = r3.text[:300]
            print(f"повтор с токеном: {r3.status_code} {r3.text[:140]}")
            session = [c for c in client.cookies.jar if c.name == ".ROBLOSECURITY"]
            result["session"] = bool(session)
            if session:
                print("  СЕССИЯ ПОЛУЧЕНА ЧЕРЕЗ HTTP")
                (VAR / f"session-{account['login']}.json").write_text(
                    json.dumps({c.name: c.value for c in client.cookies.jar},
                               ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            print("  токена нет — платформа требует интерактивного решения")
            try:
                cfg = r2.json()
                print(f"  конфигурация PX: appId={cfg.get('appId')} "
                      f"vid={str(cfg.get('vid'))[:12]} uuid={str(cfg.get('uuid'))[:12]}")
                result["px_config"] = {k: cfg.get(k) for k in
                                       ("appId", "jsClientSrc", "firstPartyEnabled", "vid", "uuid")}
            except Exception:
                pass

    (VAR / args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
