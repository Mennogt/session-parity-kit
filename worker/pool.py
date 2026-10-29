"""Пул воркеров: запуск, остановка, периодическая уборка аренд."""

from __future__ import annotations

import asyncio
import contextlib

from challenge.base import ChallengeProvider
from challenge.registry import build_provider
from core.config import Settings
from core.logging import get_logger, log
from flow.client import FlowClient
from obs import metrics
from procs import orphans
from profiles.pool import ProfilePool
from workq.base import QueueBackend
from workq.factory import build_queue
from store.store import Store
from worker.governor import RiskGovernor
from worker.runner import Worker

logr = get_logger("pool")


class WorkerPool:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.store = Store(settings.store_path)
        self.queue: QueueBackend = build_queue(settings, self.store)
        self.profiles = ProfilePool(settings, self.store)
        self.flow = FlowClient(settings)
        self.provider: ChallengeProvider = build_provider(settings)
        self.governor = (
            RiskGovernor(
                self.store,
                min_interval=settings.pair_min_interval,
                cooldown_base=settings.pair_cooldown_base,
                cooldown_max=settings.pair_cooldown_max,
            )
            if settings.pair_governor_enabled
            else None
        )
        self.stop = asyncio.Event()
        self.workers = [
            Worker(settings, self.store, self.queue, self.profiles, self.flow,
                   self.provider, index=i, governor=self.governor)
            for i in range(settings.worker_count)
        ]
        self._tasks: list[asyncio.Task[None]] = []
        self._sweepers: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        removed = orphans.cleanup()
        if removed:
            log(logr, 30, "старт: убраны осиротевшие браузеры", count=removed)
        await self.profiles.seed()
        stats = await self.profiles.stats()
        log(logr, 20, "пул профилей", **stats)
        self._tasks = [asyncio.create_task(w.run(self.stop)) for w in self.workers]
        self._sweepers = [
            asyncio.create_task(self._sweep_jobs()),
            asyncio.create_task(self._sweep_profiles()),
        ]
        log(logr, 20, "пул воркеров запущен", workers=len(self.workers),
            queue=str(self.s.queue_backend))

    async def _sweep_jobs(self) -> None:
        while not self.stop.is_set():
            await asyncio.sleep(10)
            try:
                returned = await self.queue.requeue_expired()
                if returned:
                    log(logr, 30, "возвращено по истечении аренды", count=returned)
                metrics.queue_depth.set(float(await self.queue.depth()))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log(logr, 40, "уборка аренд заданий не прошла", error=type(exc).__name__)

    async def _sweep_profiles(self) -> None:
        while not self.stop.is_set():
            await asyncio.sleep(30)
            try:
                freed = await self.profiles.sweep()
                if freed:
                    log(logr, 30, "возвращено профилей по истечении аренды", count=freed)
                await self.profiles.stats()
                orphans.cleanup()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log(logr, 40, "уборка профилей не прошла", error=type(exc).__name__)

    async def shutdown(self) -> None:
        self.stop.set()
        for task in self._sweepers:
            task.cancel()
        for task in self._tasks:
            task.cancel()
        for group in (self._sweepers, self._tasks):
            for task in group:
                with contextlib.suppress(BaseException):
                    await task
        with contextlib.suppress(Exception):
            await self.provider.aclose()
        with contextlib.suppress(Exception):
            await self.queue.close()
        orphans.cleanup()
        log(logr, 20, "пул остановлен")
