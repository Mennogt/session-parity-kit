"""Карта эндпоинтов из клиентских бандлов.

Собирает все строковые пути вида "/v1/...", "/v2/..." из скачанных
бандлов и выделяет те, что связаны с выдачей сессии, проверками и входом.

    python -m tools.paths_map
    python -m tools.paths_map --filter auth
"""

from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

BUNDLES = Path("var/js")
PATTERN = re.compile(r'["\'`](/[A-Za-z0-9_\-{}$:./]{3,70})["\'`]')
AUTH_WORDS = re.compile(
    r"login|auth|signup|session|ticket|cross|device|password|challenge|"
    r"captcha|token|verify|otp|2fa|passkey|nonce|credential|recover|restore",
    re.I,
)


def scan(folder: Path) -> collections.Counter:
    found: collections.Counter = collections.Counter()
    for path in folder.glob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in PATTERN.finditer(text):
            value = match.group(1)
            if value.startswith("//") or value.count("/") < 2:
                continue
            found[value] += 1
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", default=str(BUNDLES))
    ap.add_argument("--filter", default="auth")
    ap.add_argument("--out", default="var/paths-map.json")
    args = ap.parse_args()

    folder = Path(args.folder)
    if not folder.exists():
        print(f"нет каталога {folder}")
        return 1

    paths = scan(folder)
    rx = AUTH_WORDS if args.filter == "auth" else re.compile(args.filter, re.I)
    selected = {p: n for p, n in paths.items() if rx.search(p)}

    print(f"всего путей: {len(paths)} | по фильтру: {len(selected)}")
    for path, count in sorted(selected.items(), key=lambda item: (-item[1], item[0])):
        print(f"{count:>3}  {path}")

    Path(args.out).write_text(
        json.dumps({"total": len(paths), "selected": selected}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
