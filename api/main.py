"""Приложение API. Воркеры живут в отдельном процессе."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from api.routes import router
from core.config import Settings, get_settings
from core.logging import get_logger, setup_logging
from obs import metrics
from workq.base import QueueBackend
from workq.factory import build_queue
from store.store import Store

logr = get_logger("api.main")


@dataclass
class Container:
    settings: Settings
    store: Store
    queue: QueueBackend
    worker_count: int


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level, settings.log_json)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        store = Store(settings.store_path)
        queue = build_queue(settings, store)
        app.state.container = Container(settings, store, queue, settings.worker_count)
        logr.info("API запущен: store=%s queue=%s", settings.store_path, settings.queue_backend)

        async def _gauge_loop() -> None:
            while True:
                with contextlib.suppress(Exception):
                    metrics.queue_depth.set(float(await queue.depth()))
                    metrics.kpm_current.set(float(await asyncio.to_thread(store.recent_successes, 60.0)))
                    metrics.publish_store_snapshot(await asyncio.to_thread(store.stats))
                    pool_stats = await asyncio.to_thread(store.profile_stats)
                    metrics.profile_pool_free.set(float(pool_stats["free"]))
                await asyncio.sleep(5)

        task = asyncio.create_task(_gauge_loop())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
            with contextlib.suppress(Exception):
                await queue.close()
            logr.info("API остановлен")

    app = FastAPI(
        title="Session Parity Kit",
        version="1.0.0",
        description=(
            "Верификация сессий: воспроизведение эталонной последовательности "
            "шагов, прохождение узла проверки, подтверждение сессии контрольным запросом."
        ),
        lifespan=lifespan,
    )
    app.include_router(router)

    @app.exception_handler(Exception)
    async def _unhandled(request, exc: Exception):  # pragma: no cover - защита от 500 без тела
        logr.exception("необработанная ошибка: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"status": "INTERNAL_ERROR", "step": None, "detail": str(exc), "job_id": None},
        )

    return app


app = create_app()
