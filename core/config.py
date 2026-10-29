"""Типизированный конфиг. Валидируется на старте, падает внятно."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class QueueBackendName(StrEnum):
    SQLITE = "sqlite"
    REDIS = "redis"


class ChallengeProviderName(StrEnum):
    PLAYWRIGHT = "playwright"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="SPK_",
        extra="forbid",
        case_sensitive=False,
    )

    env: str = "dev"

    # API
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8080, ge=1, le=65535)
    metrics_port: int = Field(default=9090, ge=1, le=65535)

    # Хранилище
    store_path: Path = Path("./var/spk.db")

    # Очередь
    queue_backend: QueueBackendName = QueueBackendName.SQLITE
    redis_url: str = "redis://127.0.0.1:6379/0"
    queue_name: str = "spk:queue"
    queue_lease_seconds: int = Field(default=180, gt=0)

    # Пулы
    worker_count: int = Field(default=4, gt=0, le=512)
    profile_file: Path = Path("./var/profiles.json")
    job_attempt_limit: int = Field(default=3, gt=0)
    max_concurrent_per_host: int = Field(default=64, gt=0)

    # Губернатор пар «аккаунт + профиль».
    # Платформа разрешает вход паре с ограниченной частотой и расходует
    # кредит доверия после серии успехов (docs/GATE.md).
    pair_governor_enabled: bool = True
    pair_min_interval: float = Field(default=180.0, ge=0)
    pair_cooldown_base: float = Field(default=300.0, gt=0)
    pair_cooldown_max: float = Field(default=3600.0, gt=0)

    # Таймауты
    http_connect_timeout: float = Field(default=10.0, gt=0)
    http_read_timeout: float = Field(default=25.0, gt=0)
    challenge_timeout: float = Field(default=180.0, gt=0)
    verify_timeout: float = Field(default=20.0, gt=0)
    backoff_base: float = Field(default=0.5, gt=0)
    backoff_max: float = Field(default=30.0, gt=0)

    # Платформа
    auth_base: str = "https://auth.roblox.com"
    verify_url: str = "https://users.roblox.com/v1/users/authenticated"
    origin: str = "https://www.roblox.com"
    referer: str = "https://www.roblox.com/login"
    # Прогрев сессии: браузер всегда сначала открывает страницу входа,
    # поэтому платформа видит историю и cookie. Без прогрева обмен парой
    # приходит с требованием проверки без идентификатора.
    prime_session: bool = True

    # Подпись входа и HTTP-прохождение проверки (docs/POW.md).
    # С подписью платформа выдаёт вычислимую задачу вместо капчи, а токен
    # второй ступени отдаёт без интерактива.
    sai_enabled: bool = True
    http_challenge_enabled: bool = True
    pow_difficulty_limit: int = Field(default=5_000_000, ge=1)
    hba_nonce_url: str = "https://apis.roblox.com/hba-service/v1/getServerNonce"
    pow_puzzle_url: str = "https://apis.roblox.com/proof-of-work-service/v1/pow-puzzle"
    challenge_continue_url: str = "https://apis.roblox.com/challenge/v1/continue"
    captcha_url: str = "https://apis.roblox.com/v2/captcha?urlLocale=en_us"

    # Узел проверки
    challenge_provider: ChallengeProviderName = ChallengeProviderName.PLAYWRIGHT
    playwright_headless: bool = True
    playwright_browser: str = "chromium"
    # Браузерный путь стоит ~5 МиБ трафика на поток: включать осознанно.
    challenge_browser_enabled: bool = True
    browser_block_assets: bool = True
    px_hold_seconds: float = Field(default=12.0, gt=0)
    px_session_wait: float = Field(default=45.0, gt=0)

    # Логи
    log_level: str = "INFO"
    log_json: bool = True
    capture_fixtures: bool = False
    fixtures_dir: Path = Path("./fixtures")

    @field_validator("auth_base", "verify_url", "origin", "referer")
    @classmethod
    def _must_be_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError(f"ожидался абсолютный URL, получено: {v!r}")
        return v.rstrip("/") if v.endswith("/") else v

    @field_validator("log_level")
    @classmethod
    def _level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        up = v.upper()
        if up not in allowed:
            raise ValueError(f"log_level должен быть из {sorted(allowed)}, получено {v!r}")
        return up

    @model_validator(mode="after")
    def _cross(self) -> "Settings":
        if self.queue_backend is QueueBackendName.REDIS and not self.redis_url:
            raise ValueError("queue_backend=redis требует SPK_REDIS_URL")
        if self.backoff_max < self.backoff_base:
            raise ValueError("backoff_max должен быть >= backoff_base")
        return self

    def ensure_dirs(self) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        if self.capture_fixtures:
            self.fixtures_dir.mkdir(parents=True, exist_ok=True)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
        _settings.ensure_dirs()
    return _settings


def reset_settings() -> None:
    """Только для тестов."""
    global _settings
    _settings = None
