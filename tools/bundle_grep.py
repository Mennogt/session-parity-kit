"""Поиск по клиентским бандлам с контекстом.

Обёртка над регулярным поиском, чтобы не собирать однострочные команды и не
спотыкаться об экранирование в оболочке.

    python -m tools.bundle_grep basePath --before 9000 --file 28-
    python -m tools.bundle_grep "initializeChallenge\\(" --before 300 --after 160
    python -m tools.bundle_grep "challenge-service|challengeApi" --all
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

BUNDLES = Path("var/js")


def files(prefix: str) -> list[Path]:
    items = [p for p in BUNDLES.glob("*") if p.is_file()]
    if prefix:
        items = [p for p in items if p.name.startswith(prefix)]
    return sorted(items)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pattern")
    ap.add_argument("--file", default="", help="префикс имени файла бандла")
    ap.add_argument("--before", type=int, default=200)
    ap.add_argument("--after", type=int, default=240)
    ap.add_argument("--limit", type=int, default=6, help="сколько совпадений на файл")
    ap.add_argument("--all", action="store_true", help="поиск по всем файлам")
    args = ap.parse_args()

    rx = re.compile(args.pattern)
    total = 0
    for path in files(args.file):
        text = path.read_text(encoding="utf-8", errors="replace")
        hits = list(rx.finditer(text))
        if not hits:
            continue
        print(f"=== {path.name[:24]} : {len(hits)} совпадений ===")
        for match in hits[: args.limit]:
            start = max(0, match.start() - args.before)
            end = match.end() + args.after
            print("  ", text[start:end].replace("\n", " ")[: args.before + args.after])
            print()
            total += 1
        if not args.all:
            break
    print(f"показано совпадений: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
