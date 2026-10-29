"""Точка входа пула воркеров: отдельный процесс от API."""

from __future__ import annotations

import asyncio
import contextlib
import signal

from core.config import get_settings
from core.logging import get_logger, setup_logging
from worker import metrics_server
from worker.pool import WorkerPool


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_json)
    logr = get_logger("worker.main")
    metrics_server.start(settings.metrics_port)
    pool = WorkerPool(settings)
    await pool.start()

    stop = asyncio.Event()

    def _signal(*_: object) -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, AttributeError):
            loop.add_signal_handler(sig, _signal)

    logr.info("воркеры работают; остановка — Ctrl+C")
    await stop.wait()
    await pool.shutdown()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
