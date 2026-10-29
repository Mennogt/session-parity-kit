"""Фабрика транспорта по конфигу. Оба бэкенда полноценные."""

from __future__ import annotations

from core.config import QueueBackendName, Settings
from workq.base import QueueBackend
from workq.redis_q import RedisQueue
from workq.sqlite_q import SqliteQueue
from store.store import Store


def build_queue(settings: Settings, store: Store) -> QueueBackend:
    if settings.queue_backend is QueueBackendName.REDIS:
        return RedisQueue(
            store=store,
            url=settings.redis_url,
            name=settings.queue_name,
            lease_seconds=settings.queue_lease_seconds,
        )
    return SqliteQueue(store=store, lease_seconds=settings.queue_lease_seconds)
