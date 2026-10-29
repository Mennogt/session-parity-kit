"""Абстракция узла проверки. Одна реализация по умолчанию, реестр по имени."""

from __future__ import annotations

from typing import Protocol

from flow.detect import ChallengePrompt
from profiles.model import Profile


class ChallengeError(Exception):
    """Узел проверки не пройден."""


class ChallengeTimeout(ChallengeError):
    """Не уложились в отведённое время."""


class ProviderUnavailable(ChallengeError):
    """Реализация недоступна на этой машине (нет браузера, нет зависимостей)."""


class SessionEstablished(ChallengeError):
    """Сессия установлена прямо в браузерном контексте.

    Часть проверок (PerimeterX «нажмите и удерживайте») завершает вход внутри
    страницы: токена для повторного обмена не выдаёт, зато выставляет
    сессионные cookie. В этом случае повторный обмен не нужен — cookie уже
    в банке профиля, дальше идёт контрольный запрос.
    """


class ChallengeProvider(Protocol):
    name: str

    async def solve(self, prompt: ChallengePrompt, profile: Profile) -> str:
        """Вернуть токен провайдера для повтора обмена."""
        ...

    async def aclose(self) -> None: ...
