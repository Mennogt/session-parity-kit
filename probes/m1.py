"""M1: один поток, один аккаунт, end-to-end, через реальный прокси.

Запускает ту же реализацию потока, что и сервис (`flow.client.FlowClient`),
без очереди и без API — чтобы видеть каждый шаг и сырые ответы.

    $env:SPK_CAPTURE_FIXTURES='true'
    python -m probes.m1 --account 0 --port 10000

Узел проверки проходится браузерной реализацией, если она доступна.
Если браузера в среде нет, поток вернёт CHALLENGE_FAILED с точной причиной —
это тоже результат: значит платформа запросила проверку именно на этом шаге.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path

from challenge.registry import build_provider
from core.config import get_settings
from core.logging import setup_logging
from flow.client import FlowClient
from profiles.model import Profile
from probes.proxy_check import proxy_url

ACCOUNTS = Path("var/accounts.json")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def load_account(index: int) -> dict:
    data = json.loads(ACCOUNTS.read_text(encoding="utf-8"))
    if not 0 <= index < len(data):
        raise SystemExit(f"аккаунт #{index} вне диапазона 0..{len(data) - 1}")
    return data[index]


def mask(value: str) -> str:
    if len(value) <= 2:
        return "*" * len(value)
    return value[0] + "*" * (len(value) - 2) + value[-1]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, default=0)
    ap.add_argument("--port", type=int, default=10000)
    ap.add_argument("--proxy-url", default=None,
                    help="полный URL прокси (http://user:pass@host:port) вместо пула")
    ap.add_argument("--no-proxy", action="store_true")
    ap.add_argument("--solve", action="store_true", help="проходить узел проверки браузером")
    args = ap.parse_args()

    settings = get_settings()
    setup_logging("DEBUG" if settings.capture_fixtures else "INFO", as_json=False)

    account = load_account(args.account)
    job_id = "m1-" + uuid.uuid4().hex[:8]
    chosen_proxy = None if args.no_proxy else (args.proxy_url or proxy_url(args.port))
    profile = Profile(
        id=f"m1-{args.port}",
        proxy_url=chosen_proxy,
        user_agent=UA,
        extra_headers={
            "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        },
    )

    print(f"аккаунт:    {account['login']} (секрет {mask(account['password'])})")
    print(f"прокси:     {'нет' if args.no_proxy else (args.proxy_url or args.port)}")
    print(f"задание:    {job_id}")
    print(f"фикстуры:   {'да' if settings.capture_fixtures else 'нет'}")
    print("-" * 60)

    provider = build_provider(settings) if args.solve else None

    async def solve(prompt, prof):  # noqa: ANN001
        print(f"[challenge] требуется: {prompt.captcha_id} provider={prompt.provider}")
        if provider is None:
            raise RuntimeError(
                "узел проверки требуется, но --solve не указан: "
                "браузерная реализация не запускалась"
            )
        return await provider.solve(prompt, prof)

    flow = FlowClient(settings)
    started = time.perf_counter()
    try:
        outcome, cookies = await flow.execute(
            profile, account["login"], account["password"], job_id, solve
        )
    finally:
        if provider is not None:
            await provider.aclose()

    elapsed = int((time.perf_counter() - started) * 1000)
    print("-" * 60)
    print(f"статус:      {outcome.status}")
    print(f"шаг:         {outcome.step}")
    print(f"детали:      {outcome.detail}")
    print(f"проверка:    {outcome.challenge_passed}")
    print(f"длительность:{elapsed} ms")
    print(f"cookie:      {[c['name'] for c in outcome.cookies]}")
    print(f"банка:       {sorted(cookies)}")

    if outcome.status == "SUCCESS":
        session = [c for c in outcome.cookies if c["name"] == ".ROBLOSECURITY"]
        print(f"сессионная:  {'есть, длина ' + str(len(session[0]['value'])) if session else 'НЕТ'}")
        print(f"httpOnly:    {session[0]['httpOnly'] if session else '-'}")
        print(f"домен:       {session[0]['domain'] if session else '-'}")
        print("КОНТРОЛЬНЫЙ ЗАПРОС ПРОШЁЛ — сессия живая")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
