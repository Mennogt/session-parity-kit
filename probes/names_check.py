"""Проверка существования имён: без попыток входа.

Публичный поиск по именам отвечает, есть ли такой аккаунт. Это отсекает
опечатки до того, как мы потратим попытки входа, и не трогает чужие аккаунты.

    python -m probes.names_check --extra var/extra-pairs.txt
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
URL = "https://users.roblox.com/v1/usernames/users"


def chunks(items: list[str], size: int = 90) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def lookup(names: list[str]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    with httpx.Client(timeout=40) as client:
        for group in chunks(names):
            resp = client.post(
                URL,
                headers={
                    "User-Agent": UA,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Origin": "https://www.roblox.com",
                    "Referer": "https://www.roblox.com/",
                },
                json={"usernames": group, "excludeBannedUsers": False},
            )
            if resp.status_code != 200:
                print(f"  ответ {resp.status_code}: {resp.text[:120]}")
                continue
            for item in resp.json().get("data", []):
                key = (item.get("requestedUsername") or item.get("name") or "").lower()
                found[key] = item
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--extra", default="var/extra-pairs.txt")
    ap.add_argument("--accounts", default="var/accounts.json")
    args = ap.parse_args()

    accounts = json.loads(Path(args.accounts).read_text(encoding="utf-8"))
    main_names = [a["login"] for a in accounts]
    print(f"пул с общим паролем: {len(main_names)}")
    found_main = lookup(main_names)
    exist_main = [n for n in main_names if n.lower() in found_main]
    missing_main = [n for n in main_names if n.lower() not in found_main]
    print(f"  существуют: {len(exist_main)}, не найдены: {len(missing_main)}")
    for n in missing_main:
        print(f"    НЕТ: {n}")

    extra = Path(args.extra)
    if extra.exists():
        pairs = []
        for line in extra.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            login, _, password = line.partition(":")
            pairs.append((login, password))
        print(f"\nсписок второго пула: {len(pairs)} пар")
        emails = [l for l, _ in pairs if "@" in l]
        names = [l for l, _ in pairs if "@" not in l]
        print(f"  из них похожи на почту: {len(emails)} (поиск по именам их не находит)")
        found_extra = lookup(names)
        exist_extra = [n for n in names if n.lower() in found_extra]
        missing_extra = [n for n in names if n.lower() not in found_extra]
        print(f"  имена существуют: {len(exist_extra)}, не найдены: {len(missing_extra)}")
        if missing_extra:
            print("  не найдены (вероятные опечатки):")
            for n in missing_extra[:40]:
                print(f"    {n}")

        Path("var/names-check.json").write_text(
            json.dumps(
                {
                    "main_exist": exist_main,
                    "main_missing": missing_main,
                    "extra_exist": exist_extra,
                    "extra_missing": missing_extra,
                    "extra_email_like": emails,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print("\nсохранено: var/names-check.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
