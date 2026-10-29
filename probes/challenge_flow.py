"""Настоящий протокол проверки Roblox через HTTP.

Снято с браузера (`probes.challenge_protocol`):

1. `POST /v2/login` отвечает 403 и **заголовком** `rblx-challenge-metadata`
   (base64 JSON с `challengeId`, `redemptionToken`, `sharedParameters`).
2. Клиент идёт в `POST apis.roblox.com/v2/captcha` с телом
   `{"challenge_id": "<id>"}` и получает параметры проверки.
3. После решения повторяет обмен, прикладывая заголовки `rblx-challenge-*`
   с заполненным `redemptionToken`.

Раньше мы отправляли поля `captchaId`/`captchaToken`/`captchaProvider` —
это другой, устаревший контракт, платформа их игнорирует. Здесь проверяем
настоящий.

    python -m probes.challenge_flow --account 3
"""

from __future__ import annotations

import argparse
import asyncio
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
    "Origin": "https://www.roblox.com",
    "Referer": "https://www.roblox.com/login",
}
VAR = Path("var")


def b64json(value: str) -> dict | None:
    pad = "=" * (-len(value) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(value + pad))
    except Exception:
        try:
            return json.loads(base64.b64decode(value + pad))
        except Exception:
            return None


def challenge_headers(resp: httpx.Response) -> dict[str, str]:
    return {k: v for k, v in resp.headers.items() if k.lower().startswith("rblx-challenge")}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, default=3)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--out", default="var/challenge-flow.json")
    args = ap.parse_args()

    accounts = json.loads((VAR / "accounts.json").read_text(encoding="utf-8"))
    account = accounts[args.account]
    proxy = None
    if args.port:
        from probes.proxy_check import proxy_url

        proxy = proxy_url(args.port)

    report: dict = {"account": account["login"], "steps": []}

    async with httpx.AsyncClient(proxy=proxy, timeout=40, follow_redirects=False,
                                 headers=dict(BASE)) as client:
        # Прогрев: гостевые cookie и токен запроса.
        await client.get("https://www.roblox.com/login",
                         headers={**BASE, "Accept": "text/html"})
        r0 = await client.post("https://auth.roblox.com/v2/login",
                               json={"ctype": "Username", "cvalue": "probe_zz_none", "password": "x"})
        token = r0.headers.get("x-csrf-token")
        if token:
            client.headers["X-CSRF-TOKEN"] = token
        print(f"прогрев: токен {'есть' if token else 'нет'}, "
              f"cookie {sorted(c.name for c in client.cookies.jar)}")

        body = {"ctype": "Username", "cvalue": account["login"], "password": account["password"]}
        r1 = await client.post("https://auth.roblox.com/v2/login", json=body)
        ch = challenge_headers(r1)
        report["steps"].append({"step": "login", "status": r1.status_code,
                                "challenge_headers": ch, "body": r1.text[:200]})
        print(f"1) обмен: {r1.status_code}; заголовков проверки: {list(ch)}")

        meta_raw = ch.get("rblx-challenge-metadata") or ch.get("RBLX-CHALLENGE-METADATA")
        meta = b64json(meta_raw) if meta_raw else None
        if meta:
            report["metadata"] = meta
            print(f"   метаданные: {json.dumps(meta, ensure_ascii=False)[:400]}")
        else:
            print("   метаданных нет — дальше идти некуда")
            (VAR / args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
            return 2

        challenge_id = meta.get("challengeId") or meta.get("genericChallengeId")
        print(f"2) challenge_id: {challenge_id}")

        r2 = await client.post(
            "https://apis.roblox.com/v2/captcha?urlLocale=en_us",
            json={"challenge_id": challenge_id},
            headers={**BASE, "Content-Type": "application/json;charset=UTF-8"},
        )
        ch2 = challenge_headers(r2)
        report["steps"].append({"step": "captcha", "status": r2.status_code,
                                "challenge_headers": ch2, "body": r2.text[:800]})
        print(f"3) /v2/captcha: {r2.status_code} {r2.text[:220]!r}")
        if ch2:
            print(f"   заголовки: {list(ch2)}")

        # Если платформа сразу выдала токен — повторяем обмен с ним.
        token_found = None
        for source in (r2.text, json.dumps(ch2)):
            m = re.search(r'"(?:redemptionToken|token|captchaToken)"\s*:\s*"([^"]{20,})"', source)
            if m:
                token_found = m.group(1)
                break
        if token_found:
            print(f"4) токен получен без решения, длина {len(token_found)}")
            new_meta = dict(meta)
            new_meta["redemptionToken"] = token_found
            payload = base64.b64encode(json.dumps(new_meta).encode()).decode()
            headers = dict(BASE)
            headers["rblx-challenge-id"] = str(challenge_id)
            headers["rblx-challenge-metadata"] = payload
            if client.headers.get("X-CSRF-TOKEN"):
                headers["X-CSRF-TOKEN"] = client.headers["X-CSRF-TOKEN"]
            r3 = await client.post("https://auth.roblox.com/v2/login", json=body, headers=headers)
            report["steps"].append({"step": "login_with_token", "status": r3.status_code,
                                    "body": r3.text[:300],
                                    "cookies": sorted(c.name for c in client.cookies.jar)})
            print(f"5) повтор: {r3.status_code} {r3.text[:180]}")
            if any(c.name == ".ROBLOSECURITY" for c in client.cookies.jar):
                print("   СЕССИЯ ПОЛУЧЕНА")
                jar = {c.name: c.value for c in client.cookies.jar}
                (VAR / f"session-{account['login']}.json").write_text(
                    json.dumps(jar, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            print("4) токена в ответе нет — значит проверку всё же надо решать")

    (VAR / args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
