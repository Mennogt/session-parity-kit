"""Prometheus-экспозиция. Регистр собственный, чтобы не конфликтовать при рестарте."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry(auto_describe=True)

jobs_total = Counter(
    "spk_jobs_total",
    "Распределение статусов заданий",
    labelnames=("status",),
    registry=REGISTRY,
)

kpm_current = Gauge(
    "spk_kpm_current",
    "Подтверждённых потоков в минуту (окно 60с)",
    registry=REGISTRY,
)

captcha_solve_seconds = Histogram(
    "spk_captcha_solve_seconds",
    "Длительность узла проверки",
    buckets=(1, 2, 5, 10, 20, 30, 45, 60, 90, 120, 180),
    registry=REGISTRY,
)

login_flow_seconds = Histogram(
    "spk_login_flow_seconds",
    "Длительность полного потока",
    buckets=(0.5, 1, 2, 4, 8, 15, 30, 60, 120, 240),
    registry=REGISTRY,
)

proxy_fail_total = Counter(
    "spk_proxy_fail_total",
    "Отказы профилей",
    labelnames=("reason",),
    registry=REGISTRY,
)

queue_depth = Gauge(
    "spk_queue_depth",
    "Длина очереди",
    registry=REGISTRY,
)

worker_busy = Gauge(
    "spk_worker_busy",
    "Занятых воркеров",
    registry=REGISTRY,
)

profile_pool_free = Gauge(
    "spk_profile_pool_free",
    "Свободных профилей",
    registry=REGISTRY,
)

processes_orphaned_total = Counter(
    "spk_processes_orphaned_total",
    "Убито осиротевших браузерных процессов",
    registry=REGISTRY,
)

browser_bytes_total = Counter(
    "spk_browser_bytes_total",
    "Трафик браузерных прохождений, байты (по CDP encodedDataLength)",
    registry=REGISTRY,
)

browser_sessions_total = Counter(
    "spk_browser_sessions_total",
    "Открытых браузерных контекстов",
    labelnames=("result",),
    registry=REGISTRY,
)

step_errors_total = Counter(
    "spk_step_errors_total",
    "Ошибки по шагам потока",
    labelnames=("step", "status"),
    registry=REGISTRY,
)

jobs_by_status = Gauge(
    "spk_jobs_by_status",
    "Задания по статусу, из хранилища (переживает рестарт)",
    labelnames=("status",),
    registry=REGISTRY,
)

jobs_in_state = Gauge(
    "spk_jobs_in_state",
    "Задания по состоянию, из хранилища",
    labelnames=("state",),
    registry=REGISTRY,
)


def publish_store_snapshot(stats: dict[str, int]) -> None:
    """Отразить срез хранилища в метриках. Вызывается из процесса API."""
    for key, value in stats.items():
        if key in ("QUEUED", "RUNNING"):
            jobs_in_state.labels(state=key).set(float(value))
        else:
            jobs_by_status.labels(status=key).set(float(value))
