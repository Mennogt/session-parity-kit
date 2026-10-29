"""Пул профилей: аренда, возврат, карантин, проверка живости."""

from __future__ import annotations

import asyncio

import httpx

from core.config import Settings
from core.logging import get_logger, log
from core.statuses import Status
from obs import metrics
from profiles.model import Profile, load_profiles
from store.store import Store

logr = get_logger("profiles")
QUARANTINE_AFTER_FAILURES = 3


class ProfilePool:
    def __init__(self, settings: Settings, store: Store) -> None:
        self.s = settings
        self.store = store

    async def seed(self) -> int:
        """Загрузить профили из файла в хранилище (идемпотентно)."""
        profiles = load_profiles(self.s.profile_file)
        for p in profiles:
            await asyncio.to_thread(self.store.upsert_profile, p)
        log(logr, 20, "профили загружены", count=len(profiles), file=str(self.s.profile_file))
        return len(profiles)

    async def lease(self, worker_id: str, lease_seconds: float) -> Profile | None:
        return await asyncio.to_thread(self.store.lease_profile, worker_id, lease_seconds)

    async def release(self, profile: Profile, status: Status | None) -> None:
        """Вернуть профиль. Неудачи копятся, после порога — карантин."""
        if status is Status.PROXY_ERROR:
            profile.failures += 1
        elif status is Status.SUCCESS:
            profile.failures = 0
        if profile.failures >= QUARANTINE_AFTER_FAILURES:
            profile.quarantined = True
            await asyncio.to_thread(
                self.store.quarantine_profile, profile.id,
                f"{profile.failures} подряд отказов ({status})",
            )
            log(logr, 30, "профиль в карантине", profile=profile.id, failures=profile.failures)
            return
        await asyncio.to_thread(self.store.release_profile, profile)

    async def health_check(self, profile: Profile) -> bool:
        """Проверка живости вне активных заданий."""
        try:
            async with httpx.AsyncClient(
                proxy=profile.proxy_url or None,
                timeout=httpx.Timeout(connect=self.s.http_connect_timeout, read=15.0,
                                      write=15.0, pool=5.0),
                follow_redirects=False,
            ) as client:
                resp = await client.get(
                    f"{self.s.origin}/login",
                    headers={"User-Agent": profile.user_agent, "Accept": "text/html"},
                )
            alive = resp.status_code < 500
        except Exception as exc:
            log(logr, 30, "проверка профиля не прошла", profile=profile.id,
                error=type(exc).__name__)
            metrics.proxy_fail_total.labels(reason=f"health.{type(exc).__name__}").inc()
            alive = False
        if not alive:
            profile.failures += 1
        return alive

    async def sweep(self) -> int:
        return await asyncio.to_thread(self.store.release_expired_profiles)

    async def stats(self) -> dict[str, int]:
        out = await asyncio.to_thread(self.store.profile_stats)
        metrics.profile_pool_free.set(out["free"])
        return out
