"""Проверка HTTP-входа через мобильные адреса по нескольким аккаунтам.

Задача: понять, действительно ли мобильные адреса проходят без проверки,
и что с сессиями — выдаётся ли рабочая cookie.

    python -m probes.mobile_matrix --logins <login>,<login>,<login> --urls var/mobile-urls.txt
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.config import get_settings
from core.logging import setup_logging
from flow.client import FlowClient
from profiles.model import Profile
from probes.m1_browser import UA


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logins", required=True, help="через запятую")
    ap.add_argument("--urls", default="var/mobile-urls.txt")
    ap.add_argument("--prime", action="store_true", default=True)
    ap.add_argument("--out", default="var/mobile-matrix.json")
    args = ap.parse_args()

    settings = get_settings()
    settings.prime_session = args.prime
    setup_logging("ERROR", as_json=False)

    accounts = json.loads(Path("var/accounts.json").read_text(encoding="utf-8"))
    by_login = {a["login"].lower(): a for a in accounts}
    urls = [u for u in Path(args.urls).read_text(encoding="utf-8").splitlines() if u.strip()]

    wanted = [x.strip() for x in args.logins.split(",") if x.strip()]
    rows: list[dict] = []
    flow = FlowClient(settings)

    for i, login in enumerate(wanted):
        account = by_login.get(login.lower())
        if account is None:
            print(f"{login}: нет в пуле")
            continue
        url = urls[i % len(urls)]
        profile = Profile(id=f"mob-{i}", proxy_url=url, user_agent=UA)
        outcome, jar = flow_execute(flow, profile, account, f"mob-{i}")
        session = jar.get(".ROBLOSECURITY", "")
        rows.append({"login": account["login"], "status": str(outcome.status),
                     "step": str(outcome.step), "detail": outcome.detail[:160],
                     "session_len": len(session)})
        print(f"{account['login']:<22} {outcome.status:<18} "
              f"{('сессия ' + str(len(session))) if session else 'без сессии':<14} "
              f"{outcome.detail[:80]}")

    Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(1 for r in rows if r["status"] == "SUCCESS")
    print(f"\nSUCCESS: {ok} из {len(rows)}")
    return 0


def flow_execute(flow: FlowClient, profile: Profile, account: dict, job_id: str):  # noqa: ANN001
    import asyncio

    async def run():  # noqa: ANN202
        return await flow.execute(
            profile, account["login"], account["password"], job_id,
            lambda a, b: (_ for _ in ()).throw(RuntimeError("капча не поддерживается")),
        )

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
