"""Очередь на SQLite: тот же файл, что и хранилище.

Доставка — атомарный захват строки задания с арендой. Никакой потери при
падении воркера: аренда истекает и задание возвращается.
"""

from __future__ import annotations

import asyncio
import time

from core.statuses import JobState
from workq.base import QueueBackend
from store.store import Store


class SqliteQueue(QueueBackend):
    def __init__(self, store: Store, lease_seconds: float) -> None:
        self.store = store
        self.lease_seconds = lease_seconds

    async def put(self, job_id: str) -> None:
        # Задание уже в таблице со state=QUEUED; повторный put идемпотентен.
        row = await asyncio.to_thread(self.store.get_job, job_id)
        if row is None:
            raise KeyError(f"задание {job_id} отсутствует в хранилище")
        if row["state"] == JobState.DONE:
            return

    async def get(self, timeout: float, worker_id: str) -> str | None:
        deadline = time.monotonic() + timeout
        delay = 0.01
        while True:
            row = await asyncio.to_thread(self.store.claim_job, worker_id, self.lease_seconds)
            if row is not None:
                return str(row["job_id"])
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(delay)
            delay = min(0.5, delay * 1.6)

    async def depth(self) -> int:
        return await asyncio.to_thread(self.store.queue_depth)

    async def ack(self, job_id: str, worker_id: str) -> None:
        return None

    async def close(self) -> None:
        return None

    async def requeue_expired(self) -> int:
        return await asyncio.to_thread(self.store.requeue_expired, 0.0)
