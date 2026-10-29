"""Экспозиция метрик процесса воркера.

Счётчики заданий и тайминги живут в процессе, который их считает.
Prometheus скрейпит каждый процесс отдельно: API на своём порту,
пул воркеров на своём. Складывать их в один процесс нельзя — счётчики
в памяти не разделяются между процессами.
"""

from __future__ import annotations

import contextlib

from prometheus_client import start_http_server

from core.logging import get_logger
from obs.metrics import REGISTRY

logr = get_logger("metrics")


def start(port: int, addr: str = "0.0.0.0") -> None:
    """Поднять HTTP-эндпоинт /metrics в отдельном потоке процесса воркера."""
    with contextlib.suppress(OSError):
        start_http_server(port, addr=addr, registry=REGISTRY)
        logr.info("метрики воркера: http://%s:%s/metrics", addr, port)
