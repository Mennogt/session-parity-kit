"""M0: разведка целевой платформы. Пишет сырые ответы в fixtures/ и отчёт.

Запуск:
    python -m probes.m0

Скрипт не требует учётных данных: он снимает формы ответов на заведомо
неверную пару, отсутствие токена и отсутствие сессии. Всё, что требует
живого аккаунта, помечается как непроверенное.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx

from core.config import Settings, get_settings

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
PROBE_LOGIN = "probe_nonexistent_0001"
PROBE_SECRET = "probe_not_a_real_password"

FIXTURES = Path("fixtures")


def _dump(name: str, resp: httpx.Response, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    record = {
        "probe": name,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "url": str(resp.request.url),
        "method": resp.request.method,
        "status_code": resp.status_code,
        "headers": {k.lower(): v for k, v in resp.headers.items()},
        "body": resp.text[:200_000],
    }
    if extra:
        record.update(extra)
    (FIXTURES / f"{name}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return record


def _headers(token: str | None = None) -> dict[str, str]:
    h = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://www.roblox.com",
        "Referer": "https://www.roblox.com/login",
    }
    if token:
        h["X-CSRF-TOKEN"] = token
    return h


async def main() -> int:
    s: Settings = get_settings()
    results: dict[str, Any] = {}
    body = {"ctype": "Username", "cvalue": PROBE_LOGIN, "password": PROBE_SECRET}

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=False) as client:
        # 1. Без токена: ожидаем отказ валидации и выдачу токена.
        r1 = await client.post(f"{s.auth_base}/v2/login", headers=_headers(), content=json.dumps(body))
        results["p1"] = _dump("p1_no_csrf", r1)
        token = r1.headers.get("x-csrf-token")
        print(f"1) без токена      -> {r1.status_code} {r1.text[:120]!r}")
        print(f"   x-csrf-token    -> {token!r} len={len(token) if token else 0}")

        # 2. С токеном и заведомо неверной парой.
        r2 = await client.post(
            f"{s.auth_base}/v2/login", headers=_headers(token), content=json.dumps(body)
        )
        results["p2"] = _dump("p2_bad_credentials", r2)
        print(f"2) неверная пара   -> {r2.status_code} {r2.text[:200]!r}")
        print(f"   set-cookie      -> {[c for c in r2.headers.get_list('set-cookie')][:4]}")

        # 3. Контрольный эндпоинт без сессии.
        try:
            r3 = await client.get(
                s.verify_url,
                headers={
                    "User-Agent": UA,
                    "Accept": "application/json",
                    "Origin": s.origin,
                    "Referer": f"{s.origin}/home",
                },
            )
            results["p3"] = _dump("p3_verify_no_session", r3)
            print(f"3) контроль без сессии -> {r3.status_code} {r3.text[:200]!r}")
        except httpx.HTTPError as exc:
            results["p3"] = {"error": f"{type(exc).__name__}: {exc}"}
            print(f"3) контроль без сессии -> сетевая ошибка {exc}")

        # 4. Кандидаты в источник публичного ключа узла проверки.
        candidates = [
            "https://apis.roblox.com/captcha/v1/metadata",
            "https://apis.nrbxcdn.com/captcha/v1/metadata",
            "https://www.roblox.com/captcha/v1/metadata",
        ]
        for idx, url in enumerate(candidates):
            try:
                r = await client.get(url, headers={"User-Agent": UA, "Accept": "*/*"})
                _dump(f"p4_metadata_{idx}", r)
                print(f"4.{idx}) {url} -> {r.status_code} {r.text[:120]!r}")
                results[f"p4_{idx}"] = {"url": url, "status": r.status_code}
            except httpx.HTTPError as exc:
                results[f"p4_{idx}"] = {"url": url, "error": f"{type(exc).__name__}"}
                print(f"4.{idx}) {url} -> {type(exc).__name__}")

        # 5. Поведение при отсутствии узла проверки: явно запрашиваем его поля.
        r5 = await client.post(
            f"{s.auth_base}/v2/login",
            headers=_headers(token),
            content=json.dumps({**body, "captchaId": "probe-invalid", "captchaToken": "x",
                                "captchaProvider": "PROVIDER_ARKOSE_LABS"}),
        )
        results["p5"] = _dump("p5_fake_challenge", r5)
        print(f"5) фиктивный узел -> {r5.status_code} {r5.text[:250]!r}")

    limits = results["p1"]["headers"]
    summary = {
        "auth_base": s.auth_base,
        "csrf_header": "x-csrf-token",
        "csrf_failure_body": results["p1"]["body"][:200],
        "csrf_failure_status": results["p1"]["status_code"],
        "bad_credentials_body": results["p2"]["body"][:300],
        "bad_credentials_status": results["p2"]["status_code"],
        "verify_url": s.verify_url,
        "verify_no_session": results.get("p3", {}).get("status_code"),
        "rate_limit": {
            "limit": limits.get("x-ratelimit-limit"),
            "remaining": limits.get("x-ratelimit-remaining"),
            "reset": limits.get("x-ratelimit-reset"),
        },
        "server": limits.get("server"),
        "cf_bm_cookie": any("__cf_bm" in c for c in results["p1"]["headers"].get("set-cookie", ""))
        if isinstance(results["p1"]["headers"].get("set-cookie"), str)
        else None,
    }
    (FIXTURES / "m0-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n--- сводка ---")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
