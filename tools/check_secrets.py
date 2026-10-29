"""Проверка дерева проекта на утечки перед публикацией.

Ищет то, что не должно попасть в публичный репозиторий: значения cookie,
закрытые ключи, прокси-креды, пары «логин + пароль» из рабочего каталога.

    python -m tools.check_secrets
    python -m tools.check_secrets --strict   # учитывать и var/, и fixtures/

Код возврата 1 — найдено подозрительное, публиковать нельзя.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".pytest_cache"}
TEXT_SUFFIXES = {".md", ".py", ".json", ".txt", ".yml", ".yaml", ".ini",
                 ".toml", ".example", ".ps1", ".cfg"}

PATTERNS: dict[str, re.Pattern[str]] = {
    "cookie-значение": re.compile(r"_\|WARNING:-DO-NOT-SHARE"),
    "закрытый ключ": re.compile(r"BEGIN (EC |RSA )?PRIVATE KEY"),
    "прокси с паролем": re.compile(r"https?://[^/\s\"']+:[^/@\s\"']+@[^\s\"']+"),
    "похоже на cookie": re.compile(r"\b_?[A-Za-z0-9]{80,}\b"),
}


def collect(root: Path, strict: bool) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        parts = set(path.parts)
        if parts & SKIP_DIRS:
            continue
        if not strict and ({"var", "fixtures"} & parts):
            continue
        if path.suffix.lower() in TEXT_SUFFIXES:
            files.append(path)
    return files


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--strict", action="store_true",
                    help="проверять также var/ и fixtures/")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    accounts_file = root / "var" / "accounts.json"
    secrets: set[str] = set()
    if accounts_file.exists():
        try:
            data = json.loads(accounts_file.read_text(encoding="utf-8"))
            for row in data:
                for key in ("password", "login"):
                    value = str(row.get(key, ""))
                    if len(value) >= 6:
                        secrets.add(value)
        except Exception:
            pass

    findings: list[tuple[Path, str, int]] = []
    for path in collect(root, args.strict):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for name, rx in PATTERNS.items():
            for match in rx.finditer(text):
                snippet = match.group(0)
                # Шаблоны в коде и подсказках — не секреты.
                if any(mark in snippet for mark in "{}<>"):
                    continue
                if "user:pass" in snippet or "USER:PASSWORD" in snippet:
                    continue
                line = text.count("\n", 0, match.start()) + 1
                findings.append((path.relative_to(root), name, line))
        for secret in secrets:
            if secret in text:
                line = text.count("\n", 0, text.index(secret)) + 1
                findings.append((path.relative_to(root), "значение из accounts.json", line))

    if not findings:
        print(f"проверено файлов: {len(collect(root, args.strict))}; утечек не найдено")
        return 0

    print("НАЙДЕНО potential leaks:")
    for path, name, line in findings[:50]:
        print(f"  {path}:{line}  [{name}]")
    print(f"всего: {len(findings)}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
