"""Разбор ответов внешней платформы.

Ни одно поле не считается известным заранее: разбор терпим к вариантам
формы и возвращает максимально подробную диагностику. Всё, что не удалось
разобрать, попадает в detail, а не теряется.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from http.cookies import CookieError, SimpleCookie
from typing import Any

from core.statuses import Status

CSRF_FAILURE_MARKER = "token validation failed"
TWO_FACTOR_MARKERS = (
    "two step verification",
    "two-step verification",
    "2sv",
    "two factor",
    "verification code",
    "check your email",
    "confirm your identity",
)
# Платформа просит проверку, но не выдала идентификатор: повторить обмен
# нечем, поэтому это не «нужен узел», а провал узла.
CHALLENGE_BARE_MARKERS = (
    "challenge is required",
    "captcha required",
    "challenge required",
)
BAD_CREDENTIALS_MARKERS = (
    "incorrect username or password",
    "incorrect password",
    "invalid username or password",
    "неверн",
)


@dataclass(slots=True)
class ChallengePrompt:
    """Всё, что платформа сообщила про узел проверки."""

    captcha_id: str
    provider: str = "PROVIDER_ARKOSE_LABS"
    site_key: str | None = None
    captcha_url: str | None = None
    raw_metadata: Any = field(default=None, repr=False)


@dataclass(slots=True)
class Classification:
    """Результат разбора одного ответа обмена."""

    status: Status | None
    detail: str = ""
    challenge: ChallengePrompt | None = None
    retry_after: float | None = None
    csrf_refresh_needed: bool = False
    raw: dict[str, Any] | None = field(default=None, repr=False)


def _as_dict(body: bytes | str | dict | None) -> dict[str, Any] | None:
    if body is None:
        return None
    if isinstance(body, dict):
        return body
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    text = text.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _first_error(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {}
    errors = payload.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        return errors[0]
    return {}


def _metadata_of(error: dict[str, Any]) -> dict[str, Any] | None:
    meta = error.get("metadata")
    if meta is None:
        return None
    if isinstance(meta, str):
        try:
            decoded = json.loads(meta)
        except json.JSONDecodeError:
            return None
        return decoded if isinstance(decoded, dict) else None
    return meta if isinstance(meta, dict) else None


def _captcha_id(meta: dict[str, Any]) -> str | None:
    """Идентификатор проверки бывает строкой, объектом или вложенным полем."""
    for key in ("captchaId", "captcha_id", "captchaID"):
        if key not in meta:
            continue
        value = meta[key]
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict):
            for inner in ("id", "value", "captchaId"):
                nested = value.get(inner)
                if isinstance(nested, str) and nested:
                    return nested
    return None


def extract_challenge(payload: dict[str, Any] | None) -> ChallengePrompt | None:
    error = _first_error(payload)
    meta = _metadata_of(error)
    if not meta:
        return None
    captcha_id = _captcha_id(meta)
    if not captcha_id:
        return None
    site_key = meta.get("siteKey") or meta.get("site_key") or meta.get("publicKey")
    captcha_url = meta.get("captchaUrl") or meta.get("captcha_url") or meta.get("url")
    provider = meta.get("captchaProvider") or meta.get("captcha_provider") or "PROVIDER_ARKOSE_LABS"
    return ChallengePrompt(
        captcha_id=captcha_id,
        provider=str(provider),
        site_key=str(site_key) if site_key else None,
        captcha_url=str(captcha_url) if captcha_url else None,
        raw_metadata=meta,
    )


def challenge_from_headers(headers: dict[str, str] | Any) -> ChallengePrompt | None:
    """Узел проверки из заголовков `rblx-challenge-*`.

    Платформа присылает идентификатор и метаданные заголовками, а не в теле
    ответа. Метаданные — base64(JSON); у proof-of-work там `sessionId`, у
    капчи — `challengeId`.
    """

    def _get(name: str) -> str:
        try:
            value = headers.get(name)
        except AttributeError:
            return ""
        return str(value) if value else ""

    raw_id = _get("rblx-challenge-id")
    raw_meta = _get("rblx-challenge-metadata")
    challenge_type = _get("rblx-challenge-type")
    if not raw_id and not raw_meta:
        return None

    meta: dict[str, Any] = {}
    if raw_meta:
        pad = "=" * (-len(raw_meta) % 4)
        for decode in (base64.urlsafe_b64decode, base64.b64decode):
            try:
                loaded = json.loads(decode(raw_meta + pad))
                if isinstance(loaded, dict):
                    meta = loaded
                    break
            except Exception:
                continue

    captcha_id = str(
        meta.get("challengeId") or meta.get("sessionId") or raw_id or ""
    )
    if not captcha_id:
        return None
    site_key = meta.get("siteKey") or meta.get("site_key") or meta.get("publicKey")
    return ChallengePrompt(
        captcha_id=captcha_id,
        provider=challenge_type or "PROOF_OF_WORK",
        site_key=str(site_key) if site_key else None,
        captcha_url=None,
        raw_metadata=meta or {"raw": raw_meta},
    )


def _retry_after(headers: dict[str, str] | Any) -> float | None:

    def _get(name: str) -> str | None:
        try:
            value = headers.get(name)
        except AttributeError:
            return None
        return str(value) if value else None

    raw = _get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw.strip()))
    except ValueError:
        pass
    from email.utils import parsedate_to_datetime
    from datetime import UTC, datetime

    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    delta = (dt - datetime.now(UTC)).total_seconds()
    return max(0.0, delta)


def classify_exchange(
    status_code: int,
    headers: dict[str, str] | Any,
    body: bytes | str | dict | None,
) -> Classification:
    """Ответ на обмен пары → статус либо необходимость узла проверки."""
    payload = _as_dict(body)
    error = _first_error(payload)
    message = str(error.get("message") or (payload or {}).get("message") or "")
    lowered = message.lower()
    code = error.get("code", (payload or {}).get("code"))

    if status_code == 429:
        return Classification(
            status=Status.RATE_LIMITED,
            detail=f"лимит частоты: {message or 'без текста'}",
            retry_after=_retry_after(headers),
            raw=payload,
        )

    if status_code >= 500:
        return Classification(
            status=Status.INTERNAL_ERROR,
            detail=f"платформа ответила {status_code}",
            raw=payload,
        )

    if status_code == 403 and CSRF_FAILURE_MARKER in lowered:
        return Classification(
            status=None,
            detail="требуется согласование токена запроса",
            csrf_refresh_needed=True,
            raw=payload,
        )

    challenge = extract_challenge(payload) or challenge_from_headers(headers)
    if challenge is not None:
        return Classification(
            status=None,
            detail=f"узел проверки: {challenge.captcha_id}",
            challenge=challenge,
            raw=payload,
        )

    if any(marker in lowered for marker in CHALLENGE_BARE_MARKERS):
        return Classification(
            status=Status.CHALLENGE_FAILED,
            detail=(
                f"платформа требует проверку, но идентификатор не выдан: {message or 'без текста'}; "
                "повторить обмен нечем — нужен браузерный контекст профиля"
            ),
            raw=payload,
        )

    if any(marker in lowered for marker in TWO_FACTOR_MARKERS):
        return Classification(
            status=Status.MANUAL_REQUIRED,
            detail=message or "требуется второй фактор",
            raw=payload,
        )

    if any(marker in lowered for marker in BAD_CREDENTIALS_MARKERS):
        return Classification(status=Status.BAD_CREDENTIALS, detail=message, raw=payload)

    if status_code in (200, 201) and not error:
        return Classification(status=None, detail="обмен принят, cookie выставлены", raw=payload)

    if status_code == 403 and code in (0, 1, None):
        # 403 без внятного текста, но и без признаков проверки.
        return Classification(
            status=Status.BAD_CREDENTIALS if code == 1 else Status.INTERNAL_ERROR,
            detail=message or f"403 без классифицирующего признака (code={code})",
            raw=payload,
        )

    return Classification(
        status=Status.INTERNAL_ERROR,
        detail=f"неклассифицированный ответ {status_code}: {message or 'пустое тело'}",
        raw=payload,
    )


def _cookie_pairs_from_set_cookie(headers: Any) -> dict[str, tuple[str, int]]:
    """Значения cookie из Set-Cookie: имя → (значение, индекс вхождения)."""
    out: dict[str, tuple[str, int]] = {}
    raw: list[str] = []
    try:
        items = headers.get_list("set-cookie")
    except AttributeError:
        single = headers.get("set-cookie") if headers is not None else None
        items = [single] if single else []
    for item in items:
        if not item:
            continue
        raw.append(str(item))
    for idx, item in enumerate(raw):
        jar = SimpleCookie()
        try:
            jar.load(item)
        except CookieError:
            continue
        for name, morsel in jar.items():
            out[name] = (morsel.value, idx)
    return out


def dump_cookies(headers: Any) -> list[dict[str, Any]]:
    """Полный набор cookie ответа, включая httpOnly-признак."""
    httponly: dict[str, bool] = {}
    try:
        items = headers.get_list("set-cookie")
    except AttributeError:
        single = headers.get("set-cookie") if headers is not None else None
        items = [single] if single else []

    result: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    for item in items:
        if not item:
            continue
        item = str(item)
        jar = SimpleCookie()
        try:
            jar.load(item)
        except CookieError:
            continue
        lowered = item.lower()
        for name, morsel in jar.items():
            httponly[name] = "httponly" in lowered
            entry = {
                "name": name,
                "value": morsel.value,
                "domain": morsel["domain"] or "",
                "path": morsel["path"] or "/",
                "httpOnly": httponly[name],
            }
            seen[name] = entry
    result.extend(seen.values())
    return result
