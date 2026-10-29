"""Диагностика: что именно сервис отправляет в обмене.

Печатает заголовки и состав тела для запросов к auth.roblox.com, чтобы
сравнить с зондом, который получает проверку с идентификатором.

    python -u -m tools.debug_exchange --account example_account --proxy-url <url>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

import httpx

from core.config import Settings
from flow.client import FlowClient
from profiles.model import Profile


async def run(account: dict, proxy: str | None) -> None:
    settings = Settings()
    profile = Profile(id="dbg", proxy_url=proxy)
    client = FlowClient(settings)
    original_post = httpx.AsyncClient.post

    async def logged_post(self, url, *args, **kwargs):  # noqa: ANN001
        if "auth.roblox.com" in str(url):
            headers = kwargs.get("headers") or {}
            body = kwargs.get("content") or b""
            try:
                text = body.decode() if isinstance(body, (bytes, bytearray)) else str(body)
                parsed = json.loads(text) if text else {}
            except Exception:
                parsed = {}
            print(f"POST {url}")
            print("  поля тела:", sorted(parsed))
            print("  есть SAI:", "secureAuthenticationIntent" in parsed)
            print("  заголовки:", {k: v[:40] for k, v in headers.items()})
        resp = await original_post(self, url, *args, **kwargs)
        if 'auth.roblox.com' in str(url):
            print('  ОТВЕТ:', resp.status_code, '| заголовки проверки:',
                  {k: v[:40] for k, v in resp.headers.items() if k.startswith('rblx-')})
        return resp

    httpx.AsyncClient.post = logged_post

    async def no_browser(challenge, profile):  # noqa: ANN001
        raise RuntimeError("browser disabled")

    try:
        outcome, cookies = await client.execute(
            profile, account["login"], account["password"], "dbg", no_browser
        )
        print("ИТОГ:", outcome.status, outcome.step, outcome.detail[:120])
        print("cookie:", sorted(cookies))
    finally:
        httpx.AsyncClient.post = original_post


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="example_account")
    ap.add_argument("--proxy-url", default=None)
    args = ap.parse_args()
    os.environ.setdefault("SPK_PRIME_SESSION", "true")
    accounts = json.loads(Path("var/accounts.json").read_text(encoding="utf-8"))
    account = next(a for a in accounts if a["login"].lower() == args.account.lower())
    asyncio.run(run(account, args.proxy_url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
