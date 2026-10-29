"""Переносится ли сессия на другой адрес.

Если `.ROBLOSECURITY`, полученная на доверенном адресе, работает с другого,
то прогревать можно там, где это разрешено, а работать — где дешевле.
Опыт: вход на прямом IP, затем проверка той же cookie через прокси.

    python -m probes.session_mobility --account 0 --ports 10110,10111
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import httpx

from probes.m1_browser import UA

VAR = Path("var")
VERIFY = "https://users.roblox.com/v1/users/authenticated"
ALT = "https://www.roblox.com/mobileapi/userinfo"

LOGIN_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://www.roblox.com",
    "Referer": "https://www.roblox.com/login",
}


async def fresh_session(login: str, password: str) -> dict[str, str]:
    """Вход на прямом IP тем же кодом, что и сервис (с прогревом страницы)."""
    from core.config import get_settings
    from flow.client import FlowClient
    from profiles.model import Profile

    settings = get_settings()
    settings.prime_session = True
    profile = Profile(id="mobility", user_agent=UA)
    flow = FlowClient(settings)
    outcome, jar = await flow.execute(
        profile, login, password, "mobility",
        lambda a, b: (_ for _ in ()).throw(RuntimeError("no challenge")),
    )
    if outcome.status != "SUCCESS":
        print(f"  вход не удался: {outcome.status} {outcome.detail[:120]}")
        return {}
    return dict(jar)


async def check_session(jar: dict[str, str], proxy: str | None, label: str) -> dict:
    out = {"label": label, "proxy": bool(proxy)}
    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=40, follow_redirects=False,
                                     cookies=jar) as client:
            r = await client.get(VERIFY, headers={"User-Agent": UA, "Accept": "application/json"})
            out["verify_status"] = r.status_code
            out["identity"] = r.text[:160]
            r2 = await client.get(ALT, headers={"User-Agent": UA, "Accept": "application/json"})
            out["alt_status"] = r2.status_code
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, default=0)
    ap.add_argument("--ports", default="10110,10111")
    ap.add_argument("--attempts", type=int, default=1, help="сколько раз пытаться получить сессию")
    ap.add_argument("--interval", type=float, default=600.0, help="пауза между попытками, с")
    ap.add_argument("--out", default="var/session-mobility.json")
    args = ap.parse_args()

    accounts = json.loads((VAR / "accounts.json").read_text(encoding="utf-8"))
    account = accounts[args.account]

    jar: dict[str, str] = {}
    for attempt in range(1, args.attempts + 1):
        print(f"попытка получить сессию {attempt}/{args.attempts} "
              f"({__import__('time').strftime('%H:%M:%S')}), аккаунт {account['login']}")
        jar = await fresh_session(account["login"], account["password"])
        if jar:
            break
        if attempt < args.attempts:
            await asyncio.sleep(args.interval)
    if not jar:
        print("сессия не получена за отведённое время — опыт переносится на следующий заход")
        return 2
    print(f"  cookie: {sorted(jar)}")
    (VAR / "session-direct.json").write_text(json.dumps(jar, ensure_ascii=False, indent=2),
                                             encoding="utf-8")

    results = [await check_session(jar, None, "прямой IP")]
    print(f"  прямой IP:          verify={results[-1].get('verify_status')} "
          f"alt={results[-1].get('alt_status')}")

    from probes.proxy_check import proxy_url

    for port in [int(p) for p in args.ports.split(",") if p.strip()]:
        row = await check_session(jar, proxy_url(port), f"прокси {port}")
        results.append(row)
        print(f"  через прокси {port}: verify={row.get('verify_status')} "
              f"alt={row.get('alt_status')} {row.get('error', '')}")

    (VAR / args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                encoding="utf-8")
    direct_ok = results[0].get("verify_status") == 200
    proxy_ok = any(r.get("verify_status") == 200 for r in results[1:])
    print("\n=== вывод ===")
    if direct_ok and proxy_ok:
        print("сессия переносится: прогревать на одном адресе, работать на других")
    elif direct_ok and not proxy_ok:
        print("сессия привязана к адресу: работать можно только с того же выхода")
    else:
        print("сессия не подтвердилась даже на прямом IP — опыт некорректен")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
