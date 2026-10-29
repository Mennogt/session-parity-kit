"""Структурные логи. Секреты и значения cookie маскируются."""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

job_ctx: ContextVar[str] = ContextVar("job_id", default="-")
profile_ctx: ContextVar[str] = ContextVar("profile_id", default="-")

_SECRET_KEYS = {"password", "secret", "captchaToken", "captcha_token", "cookie_value"}
_COOKIE_RE = re.compile(r"(\.ROBLOSECURITY=)[^;\s]+", re.IGNORECASE)


def mask(text: str) -> str:
    return _COOKIE_RE.sub(r"\1<masked>", text)


def _scrub(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: ("<masked>" if k in _SECRET_KEYS else _scrub(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    if isinstance(obj, str):
        return mask(obj)
    return obj


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "msg": mask(record.getMessage()),
            "logger": record.name,
            "job_id": job_ctx.get(),
            "profile_id": profile_ctx.get(),
        }
        extra = getattr(record, "spk", None)
        if isinstance(extra, dict):
            payload.update(_scrub(extra))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class PlainFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extra = getattr(record, "spk", None)
        tail = ""
        if isinstance(extra, dict):
            tail = " " + " ".join(f"{k}={v}" for k, v in _scrub(extra).items())
        return (
            f"{datetime.now(UTC).strftime('%H:%M:%S')} {record.levelname[0]} "
            f"[{job_ctx.get()}/{profile_ctx.get()}] {mask(record.getMessage())}{tail}"
        )


_configured = False


def setup_logging(level: str = "INFO", as_json: bool = True) -> None:
    global _configured
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if as_json else PlainFormatter())
    root.addHandler(handler)
    for noisy in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, root.level))
    _configured = True


def get_logger(name: str) -> logging.Logger:
    if not _configured:
        setup_logging()
    return logging.getLogger(name)


def log(logger: logging.Logger, level: int, msg: str, **fields: Any) -> None:
    logger.log(level, msg, extra={"spk": fields})
