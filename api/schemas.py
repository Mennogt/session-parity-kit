"""Схемы API. Валидируются на входе и выходе."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from core.statuses import Status


class LoginRequest(BaseModel):
    login: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)
    job_id: str | None = Field(default=None, max_length=64)
    wait_seconds: float = Field(default=0.0, ge=0.0, le=120.0)

    @field_validator("login")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("login пуст")
        return v


class CookieOut(BaseModel):
    name: str
    value: str
    domain: str
    path: str
    httpOnly: bool


class LoginAccepted(BaseModel):
    job_id: str
    state: Literal["QUEUED", "RUNNING", "DONE"]
    status: Status | None = None
    detail: str | None = None


class JobResult(BaseModel):
    job_id: str
    login: str
    state: Literal["QUEUED", "RUNNING", "DONE"]
    status: Status | None = None
    step: str | None = None
    detail: str | None = None
    cookies: list[CookieOut] = Field(default_factory=list)
    challenge_passed: bool = False
    attempts: int = 0
    elapsed_ms: int | None = None
    retry_after: float | None = None


class HealthOut(BaseModel):
    ok: bool
    queue_depth: int
    workers: int
    profiles: dict[str, int]
    stats: dict[str, int]
    version: str = "1.0.0"
