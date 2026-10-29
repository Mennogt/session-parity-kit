"""Тесты губернатора пар.

Правила взяты из замеров: платформа разрешает вход паре не чаще интервала
и наказывает серию отказов растущей паузой.
"""

from __future__ import annotations

from pathlib import Path

from core.statuses import Status
from store.store import Store
from worker.governor import RiskGovernor


def test_new_pair_is_allowed(tmp_path: Path) -> None:
    gov = RiskGovernor(Store(tmp_path / "g.db"), min_interval=180, cooldown_base=300)
    decision = gov.check("user", "p1", now=1000.0)
    assert decision.allowed
    assert decision.wait_seconds == 0


def test_interval_blocks_second_attempt(tmp_path: Path) -> None:
    gov = RiskGovernor(Store(tmp_path / "g.db"), min_interval=180, cooldown_base=300)
    gov.record("user", "p1", Status.SUCCESS, now=1000.0)
    blocked = gov.check("user", "p1", now=1000.0 + 60)
    assert not blocked.allowed
    assert 110 <= blocked.wait_seconds <= 120


def test_interval_expires(tmp_path: Path) -> None:
    gov = RiskGovernor(Store(tmp_path / "g.db"), min_interval=180, cooldown_base=300)
    gov.record("user", "p1", Status.SUCCESS, now=1000.0)
    assert gov.check("user", "p1", now=1000.0 + 181).allowed


def test_challenge_failure_grows_cooldown(tmp_path: Path) -> None:
    gov = RiskGovernor(Store(tmp_path / "g.db"), min_interval=0, cooldown_base=300,
                       cooldown_max=3600)
    gov.record("user", "p1", Status.CHALLENGE_FAILED, now=1000.0)
    first = gov.check("user", "p1", now=1001.0)
    assert not first.allowed
    assert 290 <= first.wait_seconds <= 300

    gov.record("user", "p1", Status.CHALLENGE_FAILED, now=2000.0)
    second = gov.check("user", "p1", now=2001.0)
    assert 590 <= second.wait_seconds <= 600


def test_success_resets_failures(tmp_path: Path) -> None:
    gov = RiskGovernor(Store(tmp_path / "g.db"), min_interval=0, cooldown_base=300)
    gov.record("user", "p1", Status.CHALLENGE_FAILED, now=1000.0)
    gov.record("user", "p1", Status.SUCCESS, now=2000.0)
    row = gov.store.pair_get("user", "p1")
    assert row is not None and row["failures"] == 0


def test_proxy_error_does_not_inflate_cooldown(tmp_path: Path) -> None:
    gov = RiskGovernor(Store(tmp_path / "g.db"), min_interval=0, cooldown_base=300)
    gov.record("user", "p1", Status.PROXY_ERROR, now=1000.0)
    row = gov.store.pair_get("user", "p1")
    assert row is not None and row["failures"] == 0
    assert gov.check("user", "p1", now=1001.0).allowed


def test_pairs_are_independent(tmp_path: Path) -> None:
    gov = RiskGovernor(Store(tmp_path / "g.db"), min_interval=180, cooldown_base=300)
    gov.record("user", "p1", Status.SUCCESS, now=1000.0)
    assert not gov.check("user", "p1", now=1010.0).allowed
    assert gov.check("user", "p2", now=1010.0).allowed
    assert gov.check("other", "p1", now=1010.0).allowed
