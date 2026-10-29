"""SQLite как источник истины: задания, профили, аренды.

Один файл, WAL, короткие транзакции, доступ из нескольких процессов.
Источник истины один — очередь лишь доставляет идентификаторы.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from core.statuses import JobState, Status
from profiles.model import Profile

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id        TEXT PRIMARY KEY,
    login         TEXT NOT NULL,
    secret        TEXT NOT NULL,
    state         TEXT NOT NULL DEFAULT 'QUEUED',
    status        TEXT,
    step          TEXT,
    detail        TEXT,
    cookies       TEXT,
    challenge_passed INTEGER NOT NULL DEFAULT 0,
    attempts      INTEGER NOT NULL DEFAULT 0,
    elapsed_ms    INTEGER,
    retry_after   REAL,
    worker_id     TEXT,
    lease_until   REAL,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_state_idx ON jobs(state, created_at);
CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs(status);
CREATE INDEX IF NOT EXISTS jobs_login_idx ON jobs(login, secret, state);

CREATE TABLE IF NOT EXISTS profiles (
    id            TEXT PRIMARY KEY,
    proxy_url     TEXT,
    user_agent    TEXT NOT NULL,
    accept_language TEXT NOT NULL,
    extra_headers TEXT NOT NULL DEFAULT '{}',
    cookies       TEXT NOT NULL DEFAULT '{}',
    failures      INTEGER NOT NULL DEFAULT 0,
    quarantined   INTEGER NOT NULL DEFAULT 0,
    notes         TEXT NOT NULL DEFAULT '',
    worker_id     TEXT,
    lease_until   REAL,
    updated_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS profiles_lease_idx ON profiles(quarantined, lease_until);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS pairs (
    login         TEXT NOT NULL,
    profile_id    TEXT NOT NULL,
    last_attempt  REAL,
    last_success  REAL,
    failures      INTEGER NOT NULL DEFAULT 0,
    cooldown_until REAL,
    PRIMARY KEY (login, profile_id)
);

CREATE TABLE IF NOT EXISTS workers (
    id TEXT PRIMARY KEY,
    updated_at REAL NOT NULL
);
"""


def _now() -> float:
    return time.time()


class Store:
    """Один файл, соединение на поток, режим WAL выставлен один раз.

    Соединение кешируется в thread-local: открытие нового соединения и
    повторная установка journal_mode на каждый вызов сериализовали воркеров
    и упирали пропускную способность в единицы заданий в секунду.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        init = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            init.execute("PRAGMA journal_mode=WAL")
            init.execute("PRAGMA synchronous=NORMAL")
            init.executescript(SCHEMA)
        finally:
            init.close()

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            return conn
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # ---------------- задания ----------------

    def create_job(
        self, login: str, secret: str, job_id: str | None = None
    ) -> tuple[str, dict[str, Any]]:
        """Идемпотентно: одинаковый вход в пределах TTL даёт то же задание."""
        job_id = job_id or str(uuid.uuid4())
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM jobs WHERE login = ? AND secret = ? AND state != 'DONE' "
                    "ORDER BY created_at DESC LIMIT 1",
                    (login, secret),
                ).fetchone()
                if row is not None:
                    conn.execute("COMMIT")
                    return row["job_id"], dict(row)
                conn.execute(
                    "INSERT INTO jobs (job_id, login, secret, state, created_at, updated_at) "
                    "VALUES (?, ?, ?, 'QUEUED', ?, ?)",
                    (job_id, login, secret, now, now),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return job_id, self.get_job(job_id) or {}

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def claim_job(self, worker_id: str, lease_seconds: float) -> dict[str, Any] | None:
        """Атомарно забрать самое старое задание, свободное или с истёкшей арендой."""
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT job_id, state, lease_until FROM jobs "
                    "WHERE (state = 'QUEUED') "
                    "   OR (state = 'RUNNING' AND (lease_until IS NULL OR lease_until < ?)) "
                    "ORDER BY state != 'QUEUED', created_at LIMIT 1",
                    (now,),
                ).fetchone()
                if row is None:
                    conn.execute("COMMIT")
                    return None
                conn.execute(
                    "UPDATE jobs SET state='RUNNING', worker_id=?, lease_until=?, updated_at=? "
                    "WHERE job_id=?",
                    (worker_id, now + lease_seconds, now, row["job_id"]),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return self.get_job(row["job_id"])

    def heartbeat(self, job_id: str, lease_seconds: float) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET lease_until = ?, updated_at = ? WHERE job_id = ?",
                (_now() + lease_seconds, _now(), job_id),
            )

    def finish_job(
        self,
        job_id: str,
        status: Status,
        step: str | None,
        detail: str,
        cookies: list[dict[str, Any]],
        challenge_passed: bool,
        attempts: int,
        elapsed_ms: int,
        retry_after: float | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET state='DONE', status=?, step=?, detail=?, cookies=?, "
                "challenge_passed=?, attempts=?, elapsed_ms=?, retry_after=?, worker_id=NULL, "
                "lease_until=NULL, updated_at=? WHERE job_id=?",
                (
                    str(status),
                    step,
                    detail,
                    json.dumps(cookies, ensure_ascii=False),
                    int(challenge_passed),
                    attempts,
                    elapsed_ms,
                    retry_after,
                    _now(),
                    job_id,
                ),
            )

    def requeue_job(self, job_id: str, attempts: int, detail: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET state='QUEUED', attempts=?, detail=?, worker_id=NULL, "
                "lease_until=NULL, updated_at=? WHERE job_id=?",
                (attempts, detail, _now(), job_id),
            )

    def requeue_expired(self, grace_seconds: float = 0.0) -> int:
        """Вернуть в очередь аренды, истёкшие дольше grace. Возвращает число."""
        cutoff = _now() - grace_seconds
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE jobs SET state='QUEUED', worker_id=NULL, lease_until=NULL, updated_at=? "
                "WHERE state='RUNNING' AND lease_until IS NOT NULL AND lease_until < ?",
                (_now(), cutoff),
            )
            return cur.rowcount or 0

    def recent_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def queued_ids(self) -> list[str]:
        """Все задания, ожидающие воркера. Используется согласованием очереди."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT job_id FROM jobs WHERE state='QUEUED' ORDER BY created_at"
            ).fetchall()
        return [str(r["job_id"]) for r in rows]

    def queue_depth(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE state='QUEUED'"
            ).fetchone()
        return int(row["n"])

    def stats(self) -> dict[str, int]:
        out: dict[str, int] = {}
        with self._connect() as conn:
            for row in conn.execute(
                "SELECT status, COUNT(*) AS n FROM jobs WHERE status IS NOT NULL GROUP BY status"
            ):
                out[row["status"]] = int(row["n"])
            out["QUEUED"] = int(
                conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE state='QUEUED'").fetchone()["n"]
            )
            out["RUNNING"] = int(
                conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE state='RUNNING'").fetchone()["n"]
            )
        return out

    def recent_successes(self, window_seconds: float = 60.0) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE status='SUCCESS' AND updated_at > ?",
                (_now() - window_seconds,),
            ).fetchone()
        return int(row["n"])

    # ---------------- профили ----------------

    def upsert_profile(self, profile: Profile) -> None:
        row = profile.to_row()
        row["updated_at"] = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO profiles (id, proxy_url, user_agent, accept_language, extra_headers, "
                "cookies, failures, quarantined, notes, updated_at) "
                "VALUES (:id, :proxy_url, :user_agent, :accept_language, :extra_headers, :cookies, "
                ":failures, :quarantined, :notes, :updated_at) "
                "ON CONFLICT(id) DO UPDATE SET proxy_url=excluded.proxy_url, "
                "user_agent=excluded.user_agent, accept_language=excluded.accept_language, "
                "extra_headers=excluded.extra_headers, updated_at=excluded.updated_at",
                row,
            )

    def save_profile_state(self, profile: Profile) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE profiles SET cookies=?, failures=?, quarantined=?, notes=?, updated_at=? "
                "WHERE id=?",
                (
                    json.dumps(profile.cookies, ensure_ascii=False),
                    profile.failures,
                    int(profile.quarantined),
                    profile.notes,
                    _now(),
                    profile.id,
                ),
            )

    def lease_profile(self, worker_id: str, lease_seconds: float) -> Profile | None:
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM profiles WHERE quarantined = 0 "
                    "AND (lease_until IS NULL OR lease_until < ?) "
                    "ORDER BY failures ASC, updated_at ASC LIMIT 1",
                    (now,),
                ).fetchone()
                if row is None:
                    conn.execute("COMMIT")
                    return None
                conn.execute(
                    "UPDATE profiles SET worker_id=?, lease_until=?, updated_at=? WHERE id=?",
                    (worker_id, now + lease_seconds, now, row["id"]),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return Profile.from_row(dict(row))

    def release_profile(self, profile: Profile) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE profiles SET worker_id=NULL, lease_until=NULL, cookies=?, failures=?, "
                "quarantined=?, notes=?, updated_at=? WHERE id=?",
                (
                    json.dumps(profile.cookies, ensure_ascii=False),
                    profile.failures,
                    int(profile.quarantined),
                    profile.notes,
                    _now(),
                    profile.id,
                ),
            )

    def quarantine_profile(self, profile_id: str, reason: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE profiles SET quarantined=1, notes=?, worker_id=NULL, lease_until=NULL, "
                "updated_at=? WHERE id=?",
                (reason[:500], _now(), profile_id),
            )

    def release_expired_profiles(self) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE profiles SET worker_id=NULL, lease_until=NULL, updated_at=? "
                "WHERE lease_until IS NOT NULL AND lease_until < ?",
                (_now(), _now()),
            )
            return cur.rowcount or 0

    def profile_stats(self) -> dict[str, int]:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) AS n FROM profiles").fetchone()["n"]
            free = conn.execute(
                "SELECT COUNT(*) AS n FROM profiles WHERE quarantined=0 "
                "AND (lease_until IS NULL OR lease_until < ?)",
                (_now(),),
            ).fetchone()["n"]
            quarantined = conn.execute(
                "SELECT COUNT(*) AS n FROM profiles WHERE quarantined=1"
            ).fetchone()["n"]
        return {"total": int(total), "free": int(free), "quarantined": int(quarantined)}

    def profiles(self) -> list[Profile]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM profiles").fetchall()
        return [Profile.from_row(dict(r)) for r in rows]

    # ---------------- пары «аккаунт + профиль» ----------------

    def pair_get(self, login: str, profile_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pairs WHERE login=? AND profile_id=?", (login, profile_id)
            ).fetchone()
        return dict(row) if row else None

    def pair_record(
        self,
        login: str,
        profile_id: str,
        last_attempt: float,
        last_success: float | None,
        failures: int,
        cooldown_until: float | None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO pairs (login, profile_id, last_attempt, last_success, failures,"
                " cooldown_until) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(login, profile_id) DO UPDATE SET "
                "last_attempt=excluded.last_attempt, last_success=excluded.last_success, "
                "failures=excluded.failures, cooldown_until=excluded.cooldown_until",
                (login, profile_id, last_attempt, last_success, failures, cooldown_until),
            )

    def pair_stats(self) -> dict[str, int]:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) AS n FROM pairs").fetchone()["n"]
            cooling = conn.execute(
                "SELECT COUNT(*) AS n FROM pairs WHERE cooldown_until > ?", (_now(),)
            ).fetchone()["n"]
        return {"pairs": int(total), "cooling": int(cooling)}

    # ---------------- воркеры ----------------

    def heartbeat_worker(self, worker_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO workers (id, updated_at) VALUES (?, ?) "
                "ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at",
                (worker_id, _now()),
            )

    def active_workers(self, ttl: float = 30.0) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM workers WHERE updated_at > ?", (_now() - ttl,)
            ).fetchone()
        return int(row["n"])

    def meta_set(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def meta_get(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None
