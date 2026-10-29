"""Существуют ли аккаунты: публичный поиск по именам.

Один запрос, без авторизации, без прокси. Нужен, чтобы отделить «аккаунт
есть, но за проверкой» от «такого имени нет».
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
URL = "https://users.roblox.com/v1/usernames/users"


async def main() -> int:
    accounts = json.loads(Path("var/accounts.json").read_text(encoding="utf-8"))
    names = [a["login"] for a in accounts]
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            URL,
            headers={
                "User-Agent": UA,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Origin": "https://www.roblox.com",
                "Referer": "https://www.roblox.com/",
            },
            json={"usernames": names, "excludeBannedUsers": False},
        )
    print("код:", resp.status_code)
    found: dict[str, dict] = {}
    if resp.status_code == 200:
        payload = resp.json()
        for item in payload.get("data", []):
            name = (item.get("requestedUsername") or item.get("name") or "").lower()
            found[name] = item
        print(f"найдено: {len(found)} из {len(names)}")
        for name in names:
            item = found.get(name.lower())
            if item:
                print(f"  {name:<18} ЕСТЬ  id={item.get('id')} "
                      f"display={item.get('name')} banned={item.get('isBanned')}")
            else:
                print(f"  {name:<18} НЕТ")
    else:
        print(resp.text[:400])
    Path("var/account-existence.json").write_text(
        json.dumps({"status": resp.status_code, "found": list(found)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
