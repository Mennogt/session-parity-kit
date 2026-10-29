"""Реестр провайдеров: выбор по имени из конфига, без правок вызывающего кода."""

from __future__ import annotations

from collections.abc import Callable

from challenge.base import ChallengeProvider, ProviderUnavailable
from challenge.playwright_provider import PlaywrightProvider
from core.config import ChallengeProviderName, Settings

Factory = Callable[[Settings], ChallengeProvider]

_REGISTRY: dict[str, Factory] = {
    ChallengeProviderName.PLAYWRIGHT.value: PlaywrightProvider,
}


def register(name: str, factory: Factory) -> None:
    _REGISTRY[name] = factory


def build_provider(settings: Settings) -> ChallengeProvider:
    name = settings.challenge_provider.value
    factory = _REGISTRY.get(name)
    if factory is None:
        raise ProviderUnavailable(
            f"провайдер {name!r} не зарегистрирован; доступны: {sorted(_REGISTRY)}"
        )
    return factory(settings)
