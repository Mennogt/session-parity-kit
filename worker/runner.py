"""Один воркер: забрать задание, взять профиль, провести поток, записать итог."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from typing import Any

from challenge.base import ChallengeError, ChallengeProvider
from core.config import Settings
from core.logging import get_logger, log, job_ctx, profile_ctx
from core.statuses import (
    NO_ATTEMPT_CONSUMED,
    RETRYABLE,
    FlowOutcome,
    JobState,
    Status,
)
from flow.client import FlowClient
from obs import metrics
from profiles.pool import ProfilePool
from workq.base import QueueBackend
from store.store import Store
from worker.governor import RiskGovernor

logr = get_logger("worker")


class Worker:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        queue: QueueBackend,
        pool: ProfilePool,
        flow: FlowClient,
        provider: ChallengeProvider,
        index: int = 0,
        governor: "RiskGovernor | None" = None,
    ) -> None:
        self.s = settings
        self.store = store
        self.queue = queue
        self.pool = pool
        self.flow = flow
        self.provider = provider
        self.id = f"w{index:03d}-{uuid.uuid4().hex[:6]}"
        self.governor = governor
        self._delayed: set[asyncio.Task[None]] = set()

    # ---------- основная петля ----------

    async def run(self, stop: asyncio.Event) -> None:
        log(logr, 20, "воркер запущен", worker=self.id)
        try:
            last_hb = 0.0
            while not stop.is_set():
                now = time.monotonic()
                if now - last_hb > 5.0:
                    last_hb = now
                    with contextlib.suppress(Exception):
                        await asyncio.to_thread(self.store.heartbeat_worker, self.id)
                try:
                    job_id = await self.queue.get(timeout=1.0, worker_id=self.id)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log(logr, 40, "очередь недоступна", worker=self.id, error=type(exc).__name__)
                    await asyncio.sleep(min(self.s.backoff_max, 2.0))
                    continue
                if job_id is None:
                    continue
                metrics.worker_busy.inc()
                try:
                    await self.handle(job_id)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # воркер не должен умирать от одного задания
                    log(logr, 40, "задание уронило воркер", job=job_id,
                        error=type(exc).__name__, detail=str(exc)[:400])
                    await asyncio.to_thread(
                        self.store.finish_job, job_id, Status.INTERNAL_ERROR, None,
                        f"{type(exc).__name__}: {exc}", [], False, 0, 0, None,
                    )
                finally:
                    metrics.worker_busy.dec()
                    for task in list(self._delayed):
                        if task.done():
                            self._delayed.discard(task)
        finally:
            for task in list(self._delayed):
                task.cancel()
            log(logr, 20, "воркер остановлен", worker=self.id)

    # ---------- обработка одного задания ----------

    async def handle(self, job_id: str) -> None:
        job = await asyncio.to_thread(self.store.get_job, job_id)
        if job is None:
            log(logr, 40, "задание не найдено в хранилище", job=job_id)
            return
        if job["state"] == JobState.DONE:
            await self.queue.ack(job_id, self.id)
            return

        token_job = job_ctx.set(job_id[:8])
        started = time.perf_counter()
        attempts = int(job["attempts"] or 0) + 1
        profile: Any = None
        lease = max(self.s.queue_lease_seconds, int(self.s.challenge_timeout) + 60)

        try:
            profile = await self._acquire_profile(timeout=30.0)
            if profile is None:
                # Нет свободного профиля — задание не тратит попытку.
                log(logr, 30, "нет свободного профиля, задание возвращено", job=job_id)
                await asyncio.to_thread(
                    self.store.requeue_job, job_id, int(job["attempts"] or 0),
                    "нет свободного профиля",
                )
                await self._delayed_requeue(job_id, 1.0)
                return
            profile_ctx.set(profile.id)

            if self.governor is not None:
                decision = await asyncio.to_thread(
                    self.governor.check, job["login"], profile.id, time.time()
                )
                if not decision.allowed:
                    # Пара в паузе: не тратим попытку и не злим платформу.
                    log(logr, 20, "пара в паузе", reason=decision.reason,
                        wait_s=round(decision.wait_seconds, 1))
                    await self.pool.release(profile, None)
                    profile = None
                    await asyncio.to_thread(
                        self.store.requeue_job, job_id, int(job["attempts"] or 0),
                        f"пауза пары: {decision.reason}",
                    )
                    await self._delayed_requeue(job_id, min(decision.wait_seconds, 300))
                    return

            heartbeat = asyncio.create_task(self._heartbeat(job_id, lease))
            try:
                outcome, cookies = await self.flow.execute(
                    profile,
                    job["login"],
                    job["secret"],
                    job_id,
                    lambda prompt, prof: self.provider.solve(prompt, prof),
                )
            finally:
                heartbeat.cancel()
                with contextlib.suppress(BaseException):
                    await heartbeat

            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if self.governor is not None:
                await asyncio.to_thread(
                    self.governor.record, job["login"], profile.id, outcome.status, time.time()
                )
            await self._settle(job_id, outcome, attempts, elapsed_ms, int(job["attempts"] or 0))

            if outcome.status is Status.PROXY_ERROR and profile.proxy_url:
                log(logr, 30, "отказ профиля", profile=profile.id, detail=outcome.detail)
            await self.pool.release(profile, outcome.status)
            profile = None

        except ChallengeError as exc:
            await self._settle(
                job_id,
                FlowOutcome(Status.CHALLENGE_FAILED, None, f"{type(exc).__name__}: {exc}"),
                attempts,
                int((time.perf_counter() - started) * 1000),
                int(job["attempts"] or 0),
            )
        finally:
            if profile is not None:
                await self.pool.release(profile, None)
            await self.queue.ack(job_id, self.id)
            job_ctx.reset(token_job)
            profile_ctx.set("-")

    async def _acquire_profile(self, timeout: float) -> Any:
        """Ждать свободный профиль, а не молотить очередь вхолостую.

        Профиль — это сессия: один профиль обслуживает одно задание за раз.
        При нехватке профилей воркер ждёт освобождения.
        """
        lease = max(self.s.queue_lease_seconds, int(self.s.challenge_timeout) + 60)
        deadline = asyncio.get_running_loop().time() + timeout
        delay = 0.02
        while asyncio.get_running_loop().time() < deadline:
            profile = await self.pool.lease(self.id, lease)
            if profile is not None:
                return profile
            await asyncio.sleep(delay)
            delay = min(0.5, delay * 1.5)
        return None

    async def _settle(
        self,
        job_id: str,
        outcome: FlowOutcome,
        attempts: int,
        elapsed_ms: int,
        prior_attempts: int,
    ) -> None:
        status = outcome.status
        metrics.jobs_total.labels(status=str(status)).inc()

        if status is Status.SUCCESS:
            metrics.kpm_current.set(float(await asyncio.to_thread(self.store.recent_successes, 60.0)))
            await asyncio.to_thread(
                self.store.finish_job, job_id, status, str(outcome.step), outcome.detail,
                outcome.cookies, outcome.challenge_passed, attempts, elapsed_ms,
                outcome.retry_after,
            )
            log(logr, 20, "задание завершено успехом", status=str(status),
                elapsed_ms=elapsed_ms, attempts=attempts)
            return

        consumed = 0 if status in NO_ATTEMPT_CONSUMED else 1
        total_attempts = prior_attempts + consumed

        if status in RETRYABLE and total_attempts < self.s.job_attempt_limit:
            delay = min(
                self.s.backoff_max,
                self.s.backoff_base * (2 ** max(0, total_attempts - 1)),
            )
            if status is Status.RATE_LIMITED and outcome.retry_after:
                delay = min(self.s.backoff_max, outcome.retry_after)
            await asyncio.to_thread(
                self.store.requeue_job, job_id, total_attempts,
                f"повтор после {status}: {outcome.detail}",
            )
            log(logr, 30, "задание возвращено в очередь",
                status=str(status), attempts=total_attempts, delay_s=round(delay, 2))
            await self._delayed_requeue(job_id, delay)
            return

        await asyncio.to_thread(
            self.store.finish_job, job_id, status, str(outcome.step) if outcome.step else None,
            outcome.detail, outcome.cookies, outcome.challenge_passed, total_attempts,
            elapsed_ms, outcome.retry_after,
        )
        log(logr, 30, "задание завершено", status=str(status), attempts=total_attempts,
            elapsed_ms=elapsed_ms, detail=outcome.detail[:200])

    async def _delayed_requeue(self, job_id: str, delay: float) -> None:
        async def _later() -> None:
            await asyncio.sleep(delay)
            with contextlib.suppress(Exception):
                await self.queue.put(job_id)

        task = asyncio.create_task(_later())
        self._delayed.add(task)
        task.add_done_callback(self._delayed.discard)

    async def _heartbeat(self, job_id: str, lease: float) -> None:
        interval = max(5.0, lease / 3)
        while True:
            await asyncio.sleep(interval)
            with contextlib.suppress(Exception):
                await asyncio.to_thread(self.store.heartbeat, job_id, lease)
