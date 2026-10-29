"""Профиль исполнения: сеть, отпечаток, cookie-банка.

Один профиль — одна сессия. Параллельное использование запрещено арендой.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)


@dataclass
class Profile:
    id: str
    proxy_url: str | None = None
    user_agent: str = DEFAULT_UA
    accept_language: str = "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"
    extra_headers: dict[str, str] = field(default_factory=dict)
    cookies: dict[str, str] = field(default_factory=dict)
    failures: int = 0
    quarantined: bool = False
    notes: str = ""

    def cookie_dict(self) -> dict[str, str]:
        return dict(self.cookies)

    def absorb(self, jar: Iterable[Any]) -> None:
        """Перенести cookie из клиента в банку профиля.

        Гостевые cookie платформа выдаёт и при отказе; они не затирают
        сессионные, потому что лежат под своими именами.
        """
        merged = dict(self.cookies)
        for cookie in jar:
            name = getattr(cookie, "name", None)
            value = getattr(cookie, "value", None)
            if name and value is not None:
                merged[name] = value
        self.cookies = merged

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "proxy_url": self.proxy_url,
            "user_agent": self.user_agent,
            "accept_language": self.accept_language,
            "extra_headers": json.dumps(self.extra_headers, ensure_ascii=False),
            "cookies": json.dumps(self.cookies, ensure_ascii=False),
            "failures": self.failures,
            "quarantined": int(self.quarantined),
            "notes": self.notes,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Profile":
        return cls(
            id=str(row["id"]),
            proxy_url=row.get("proxy_url"),
            user_agent=row.get("user_agent") or DEFAULT_UA,
            accept_language=row.get("accept_language") or "ru-RU,ru;q=0.9,en;q=0.8",
            extra_headers=json.loads(row.get("extra_headers") or "{}"),
            cookies=json.loads(row.get("cookies") or "{}"),
            failures=int(row.get("failures") or 0),
            quarantined=bool(row.get("quarantined")),
            notes=row.get("notes") or "",
        )

    def __repr__(self) -> str:  # pragma: no cover
        return f"Profile(id={self.id!r}, proxy={bool(self.proxy_url)}, failures={self.failures})"


def load_profiles(path: Path) -> list[Profile]:
    """Читает файл профилей: [{id, proxy, user_agent, ...}]."""
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path}: ожидался список профилей")
    out: list[Profile] = []
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"{path}[{idx}]: ожидался объект профиля")
        out.append(
            Profile(
                id=str(item.get("id") or f"p{idx:04d}"),
                proxy_url=item.get("proxy"),
                user_agent=item.get("user_agent") or DEFAULT_UA,
                accept_language=item.get("accept_language")
                or "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                extra_headers=item.get("headers") or {},
                cookies=item.get("cookies") or {},
            )
        )
    return out
