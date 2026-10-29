"""Перебор входных точек и форм запроса: где гейта проверки нет.

Идея: гейт PerimeterX включён не на каждом маршруте и не для каждой формы
запроса. Если найдётся маршрут, который отказывает по паре
(`Incorrect username or password`) вместо `Challenge is required`, значит
обмен учётными данными там проходит без проверки — и браузер не нужен.

Все пробы идут с **неверным** паролем: успешного входа не происходит,
аккаунт не расходуется. Отличаем только два исхода: есть гейт или нет.

    python -m probes.endpoint_matrix --account 3
    python -m probes.endpoint_matrix --account 3 --port 10110
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx

PROBE_PASSWORD = "endpoint_matrix_probe_wrong"
UA_BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
UA_APP = "Roblox/WinInet"
UA_ANDROID = "Roblox/Android 2.619.1"

BASE_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://www.roblox.com",
    "Referer": "https://www.roblox.com/login",
}


def cases(login: str) -> list[dict]:
    """Набор проб: маршрут × форма × отпечаток заголовков."""
    c: list[dict] = []

    def add(name: str, method: str, url: str, **kw) -> None:
        c.append({"name": name, "method": method, "url": url, **kw})

    json_body = {"ctype": "Username", "cvalue": login, "password": PROBE_PASSWORD}
    form_body = {"ctype": "Username", "cvalue": login, "password": PROBE_PASSWORD}

    # 1. Базовая точка, разные отпечатки заголовков.
    add("v2 json browser", "POST", "https://auth.roblox.com/v2/login",
        json=json_body, ua=UA_BROWSER)
    add("v2 json app-ua", "POST", "https://auth.roblox.com/v2/login",
        json=json_body, ua=UA_APP)
    add("v2 json android-ua", "POST", "https://auth.roblox.com/v2/login",
        json=json_body, ua=UA_ANDROID)
    add("v2 form browser", "POST", "https://auth.roblox.com/v2/login",
        data=form_body, ua=UA_BROWSER,
        content_type="application/x-www-form-urlencoded")
    add("v2 form app-ua", "POST", "https://auth.roblox.com/v2/login",
        data=form_body, ua=UA_APP,
        content_type="application/x-www-form-urlencoded")

    # 2. Другие версии и маршруты той же авторизации.
    for path in ("/v1/login", "/v2/authentication/login", "/v1/authentication/login",
                 "/v2/sessions", "/v1/sessions"):
        add(f"auth{path}", "POST", f"https://auth.roblox.com{path}",
            json=json_body, ua=UA_BROWSER)

    # 3. Легаси-вход на веб-хосте.
    add("mobileapi login", "POST", "https://www.roblox.com/mobileapi/login",
        json=json_body, ua=UA_APP)
    add("mobileapi login form", "POST", "https://www.roblox.com/mobileapi/login",
        data=form_body, ua=UA_APP,
        content_type="application/x-www-form-urlencoded")
    add("Login/NewLogin", "POST", "https://www.roblox.com/Login/NewLogin",
        data=form_body, ua=UA_BROWSER,
        content_type="application/x-www-form-urlencoded")
    add("api/login", "POST", "https://www.roblox.com/api/login",
        json=json_body, ua=UA_BROWSER)

    # 4. OAuth: парольный grant (если включён — гейта может не быть).
    add("oauth token password", "POST", "https://apis.roblox.com/oauth/v1/token",
        data={"grant_type": "password", "username": login,
              "password": PROBE_PASSWORD, "client_id": "1"},
        ua=UA_BROWSER, content_type="application/x-www-form-urlencoded")

    # 5. Тот же обмен, но с ctype Email/PhoneNumber — иная ветка разбора.
    for ctype in ("Email", "PhoneNumber"):
        add(f"v2 ctype {ctype}", "POST", "https://auth.roblox.com/v2/login",
            json={"ctype": ctype, "cvalue": login, "password": PROBE_PASSWORD},
            ua=UA_BROWSER)

    # 6. Без Origin/Referer и без sec-* заголовков: иногда гейт привязан к
    #    «браузерности» запроса, а не к самому маршруту.
    add("v2 bare headers", "POST", "https://auth.roblox.com/v2/login",
        json=json_body, ua=UA_BROWSER, bare=True)

    return c


async def run_case(case: dict, proxy: str | None, timeout: float) -> dict:
    headers = {"User-Agent": case.get("ua", UA_BROWSER)}
    if not case.get("bare"):
        headers.update(BASE_HEADERS)
    if case.get("content_type"):
        headers["Content-Type"] = case["content_type"]
    else:
        headers["Content-Type"] = "application/json;charset=UTF-8"

    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            proxy=proxy,
            timeout=httpx.Timeout(connect=timeout, read=timeout, write=timeout, pool=timeout),
            follow_redirects=False,
        ) as client:
            kwargs: dict = {"headers": headers}
            if "json" in case:
                kwargs["json"] = case["json"]
            elif "data" in case:
                kwargs["data"] = case["data"]
            resp = await client.request(case["method"], case["url"], **kwargs)

            # Токен запроса, если платформа его выдала.
            token = resp.headers.get("x-csrf-token")
            if token:
                headers2 = dict(headers)
                headers2["X-CSRF-TOKEN"] = token
                kwargs2 = {k: v for k, v in kwargs.items() if k != "headers"}
                resp = await client.request(case["method"], case["url"],
                                            headers=headers2, **kwargs2)

            body = resp.text[:300]
            csrf_failed = "token validation failed" in body.lower()
            gated = "challenge is required" in body.lower()
            creds = "incorrect username or password" in body.lower()
            verdict = ("csrf-only" if csrf_failed else
                       "GATE" if gated else
                       "NO GATE" if creds else
                       f"other:{resp.status_code}")
            return {
                "name": case["name"], "method": case["method"], "url": case["url"],
                "status": resp.status_code, "verdict": verdict,
                "session_cookie": any(c.name == ".ROBLOSECURITY" for c in client.cookies.jar),
                "body": body, "ms": int((time.perf_counter() - started) * 1000),
            }
    except Exception as exc:
        return {"name": case["name"], "url": case["url"], "error":
                f"{type(exc).__name__}: {str(exc)[:120]}",
                "ms": int((time.perf_counter() - started) * 1000)}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, default=3)
    ap.add_argument("--port", type=int, default=None, help="порт прокси; без него — прямой IP")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--out", default="var/endpoint-matrix.json")
    args = ap.parse_args()

    accounts = json.loads(Path("var/accounts.json").read_text(encoding="utf-8"))
    login = accounts[args.account]["login"]
    proxy = None
    if args.port:
        from probes.proxy_check import proxy_url

        proxy = proxy_url(args.port)
    print(f"логин для проб: {login}; выход: {'прямой IP' if proxy is None else f'порт {args.port}'}")

    sem = asyncio.Semaphore(args.concurrency)
    results: list[dict] = []

    async def one(case: dict) -> None:
        async with sem:
            row = await run_case(case, proxy, args.timeout)
            results.append(row)
            verdict = row.get("verdict", "ERROR")
            print(f"  {verdict:<10} {row['name']:<28} {row.get('status', '-')} "
                  f"{row['ms']}мс {row.get('body', row.get('error', ''))[:70]!r}")

    await asyncio.gather(*(one(c) for c in cases(login)))
    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== без гейта ===")
    free = [r for r in results if r.get("verdict") == "NO GATE"]
    for r in free:
        print(f"  {r['name']}: {r['url']} -> {r['body'][:120]}")
    if not free:
        print("  ни одного: гейт стоит на всех проверенных маршрутах")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
