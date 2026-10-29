"""Проверка сервиса целиком: вход через FlowClient без браузера.

Запускает полный путь так, как его выполняет воркер: подпись, обмен,
прохождение проверки по HTTP, контрольный запрос.

    python -u -m tools.try_flow --account example_account --proxy-url <url>
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from core.config import Settings
from flow.client import FlowClient
from profiles.model import Profile


async def run(account: dict, proxy: str | None) -> dict:
    settings = Settings()
    profile = Profile(id="probe", proxy_url=proxy)
    client = FlowClient(settings)

    async def no_browser(challenge, profile):  # noqa: ANN001
        raise RuntimeError("браузерный путь отключён в этой проверке")

    outcome, cookies = await client.execute(
        profile, account["login"], account["password"], "probe-1", no_browser
    )
    session = cookies.get(".ROBLOSECURITY", "")
    return {
        "login": account["login"],
        "status": str(outcome.status),
        "step": str(outcome.step),
        "detail": outcome.detail[:160],
        "challenge_passed": outcome.challenge_passed,
        "session_len": len(session),
        "cookies": sorted(cookies),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="example_account")
    ap.add_argument("--proxy-url", default=None)
    ap.add_argument("--out", default="var/try-flow.json")
    args = ap.parse_args()

    accounts = json.loads(Path("var/accounts.json").read_text(encoding="utf-8"))
    account = next(a for a in accounts if a["login"].lower() == args.account.lower())
    result = asyncio.run(run(account, args.proxy_url))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
