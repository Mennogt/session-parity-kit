"""Единый контракт результата. Ровно один статус на задание."""

from __future__ import annotations

from enum import StrEnum


class Status(StrEnum):
    SUCCESS = "SUCCESS"
    BAD_CREDENTIALS = "BAD_CREDENTIALS"
    CHALLENGE_FAILED = "CHALLENGE_FAILED"
    MANUAL_REQUIRED = "MANUAL_REQUIRED"
    PROXY_ERROR = "PROXY_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    INVALID_SESSION = "INVALID_SESSION"
    INTERNAL_ERROR = "INTERNAL_ERROR"


TERMINAL: frozenset[Status] = frozenset(
    {
        Status.SUCCESS,
        Status.BAD_CREDENTIALS,
        Status.MANUAL_REQUIRED,
    }
)

RETRYABLE: frozenset[Status] = frozenset(
    {
        Status.CHALLENGE_FAILED,
        Status.PROXY_ERROR,
        Status.RATE_LIMITED,
        Status.INVALID_SESSION,
    }
)

# Сетевой сбой профиля не расходует попытку задания.
NO_ATTEMPT_CONSUMED: frozenset[Status] = frozenset({Status.PROXY_ERROR})

STEPS: tuple[str, ...] = ("csrf", "exchange", "challenge", "retry", "verify")


class Step(StrEnum):
    CSRF = "csrf"
    EXCHANGE = "exchange"
    CHALLENGE = "challenge"
    RETRY = "retry"
    VERIFY = "verify"


class JobState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    DONE = "DONE"


class FlowOutcome:
    """Результат одного прогона потока."""

    __slots__ = ("status", "step", "detail", "cookies", "challenge_passed", "attempts", "retry_after")

    def __init__(
        self,
        status: Status,
        step: Step | None = None,
        detail: str = "",
        cookies: list[dict] | None = None,
        challenge_passed: bool = False,
        attempts: int = 1,
        retry_after: float | None = None,
    ) -> None:
        self.status = status
        self.step = step
        self.detail = detail
        self.cookies = cookies or []
        self.challenge_passed = challenge_passed
        self.attempts = attempts
        self.retry_after = retry_after

    def __repr__(self) -> str:  # pragma: no cover - диагностика
        return (
            f"FlowOutcome(status={self.status}, step={self.step}, "
            f"detail={self.detail!r}, attempts={self.attempts})"
        )
