"""Очередь на Redis для нескольких хостов.

Список ожидания + список обработки на воркера. Хранилище остаётся
источником истины: согласование (`reconcile`) возвращает в список всё, что
хранилище считает ожидающим, поэтому потеря сообщения не теряет задание.
"""

from __future__ import annotations

import asyncio
import contextlib
import time

import redis.asyncio as aioredis

from core.logging import get_logger
from workq.base import QueueBackend
from store.store import Store

logr = get_logger("queue.redis")


class RedisQueue(QueueBackend):
    def __init__(self, store: Store, url: str, name: str, lease_seconds: float) -> None:
        self.store = store
        self.url = url
        self.name = name
        self.lease_seconds = lease_seconds
        self._client: aioredis.Redis | None = None

    @property
    def client(self) -> aioredis.Redis:
        if self._client is None:
            self._client = aioredis.from_url(
                self.url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=15,
            )
        return self._client

    @property
    def _claimed_set(self) -> str:
        return f"{self.name}:claimed"

    def _processing(self, worker_id: str) -> str:
        return f"{self.name}:processing:{worker_id}"

    async def put(self, job_id: str) -> None:
        row = await asyncio.to_thread(self.store.get_job, job_id)
        if row is None:
            raise KeyError(f"задание {job_id} отсутствует в хранилище")
        added = await self.client.sadd(self._claimed_set, job_id)
        if added:
            await self.client.lpush(self.name, job_id)

    async def get(self, timeout: float, worker_id: str) -> str | None:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            item = await self.client.brpoplpush(
                self.name, self._processing(worker_id), timeout=max(1, int(remaining))
            )
            if item is None:
                continue
            await self.client.srem(self._claimed_set, item)
            return str(item)

    async def ack(self, job_id: str, worker_id: str) -> None:
        with contextlib.suppress(Exception):
            await self.client.lrem(self._processing(worker_id), 0, job_id)

    async def depth(self) -> int:
        return int(await self.client.llen(self.name))

    async def reconcile(self) -> int:
        """Вернуть в список всё, что хранилище считает ожидающим."""
        requeued = await asyncio.to_thread(self.store.requeue_expired, 0.0)
        pending = await asyncio.to_thread(self.store.queued_ids)
        pushed = 0
        for job_id in pending:
            if await self.client.sadd(self._claimed_set, job_id):
                await self.client.rpush(self.name, job_id)
                pushed += 1
        if pushed:
            logr.info("согласование очереди: возвращено %s заданий", pushed)
        return requeued + pushed

    async def requeue_expired(self) -> int:
        return await self.reconcile()

    async def close(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.aclose()
            self._client = None
