"""Тесты хранилища, очереди и аренды профилей."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from core.statuses import Status
from profiles.model import Profile
from store.store import Store
from workq.sqlite_q import SqliteQueue


def test_job_idempotent(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    a, _ = store.create_job("user", "pass")
    b, _ = store.create_job("user", "pass")
    assert a == b


def test_claim_and_finish(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    job_id, _ = store.create_job("user", "pass")
    claimed = store.claim_job("w1", 60)
    assert claimed is not None
    assert claimed["job_id"] == job_id
    assert claimed["state"] == "RUNNING"
    store.finish_job(job_id, Status.SUCCESS, "verify", "ok", [], True, 1, 100, None)
    done = store.get_job(job_id)
    assert done is not None and done["status"] == "SUCCESS"
    assert store.claim_job("w2", 60) is None


def test_lease_expiry_returns_job(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    job_id, _ = store.create_job("user", "pass")
    claimed = store.claim_job("w1", 0.01)
    assert claimed is not None
    import time

    time.sleep(0.05)
    assert store.requeue_expired() == 1
    again = store.claim_job("w2", 60)
    assert again is not None and again["job_id"] == job_id


def test_profile_lease_is_exclusive(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    store.upsert_profile(Profile(id="p1", proxy_url=None))
    first = store.lease_profile("w1", 60)
    assert first is not None
    assert store.lease_profile("w2", 60) is None


def test_quarantine_removes_from_pool(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    store.upsert_profile(Profile(id="p1"))
    store.quarantine_profile("p1", "3 отказа")
    assert store.lease_profile("w1", 60) is None
    assert store.profile_stats()["quarantined"] == 1


def test_sqlite_queue_roundtrip(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")

    async def scenario() -> None:
        q = SqliteQueue(store, lease_seconds=60)
        job_id, _ = store.create_job("user", "pass")
        await q.put(job_id)
        got = await q.get(timeout=2, worker_id="w1")
        assert got == job_id
        assert await q.depth() == 0

    asyncio.run(scenario())
