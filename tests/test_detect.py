"""Тесты разбора ответов. Используют сохранённые сырые ответы платформы."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.statuses import Status
from flow.detect import classify_exchange, dump_cookies, extract_challenge

FIX = Path("fixtures")


def _fixture(name: str) -> dict:
    path = FIX / f"{name}.json"
    if not path.exists():
        pytest.skip(f"нет фикстуры {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def test_csrf_failure_is_not_a_status() -> None:
    fx = _fixture("p1_no_csrf")
    cls = classify_exchange(fx["status_code"], fx["headers"], fx["body"])
    assert cls.status is None
    assert cls.csrf_refresh_needed is True
    assert "токен" in cls.detail.lower()


def test_bad_credentials() -> None:
    fx = _fixture("p2_bad_credentials")
    cls = classify_exchange(fx["status_code"], fx["headers"], fx["body"])
    assert cls.status is Status.BAD_CREDENTIALS


def test_challenge_detected_from_metadata_string() -> None:
    body = json.dumps(
        {
            "errors": [
                {
                    "code": 0,
                    "message": "captcha required",
                    "metadata": json.dumps({"captchaId": "abc-123"}),
                }
            ]
        }
    )
    cls = classify_exchange(403, {}, body)
    assert cls.status is None
    assert cls.challenge is not None
    assert cls.challenge.captcha_id == "abc-123"


def test_challenge_detected_from_nested_object() -> None:
    body = {
        "errors": [
            {
                "code": 0,
                "message": "challenge",
                "metadata": {
                    "captchaId": {"id": "nested-9"},
                    "captchaProvider": "PROVIDER_ARKOSE_LABS",
                    "siteKey": "476068BF-9607-4799-B53D-966BE98E2B81",
                },
            }
        ]
    }
    prompt = extract_challenge(body)
    assert prompt is not None
    assert prompt.captcha_id == "nested-9"
    assert prompt.site_key == "476068BF-9607-4799-B53D-966BE98E2B81"


def test_rate_limit_with_retry_after() -> None:
    cls = classify_exchange(429, {"retry-after": "12"}, b"{}")
    assert cls.status is Status.RATE_LIMITED
    assert cls.retry_after == 12.0


def test_two_factor_goes_manual() -> None:
    body = {"errors": [{"code": 0, "message": "Two Step Verification required"}]}
    assert classify_exchange(403, {}, body).status is Status.MANUAL_REQUIRED


def test_cookie_dump_marks_httponly() -> None:
    class H:
        def __init__(self, items):
            self._items = items

        def get_list(self, name):
            return self._items if name == "set-cookie" else []

        def get(self, name):
            return None

    header = (
        ".ROBLOSECURITY=abc; Domain=.roblox.com; Path=/; HttpOnly; Secure; "
        "Expires=Wed, 21 Oct 2026 07:28:00 GMT"
    )
    cookies = dump_cookies(H([header]))
    assert len(cookies) == 1
    assert cookies[0]["name"] == ".ROBLOSECURITY"
    assert cookies[0]["httpOnly"] is True
    assert cookies[0]["domain"] == ".roblox.com"
