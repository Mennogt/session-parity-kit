# Architecture

This document describes the service internals: modules, data model, job
lifecycle, risk governor and failure points. Russian version:
[ARCHITECTURE.md](ARCHITECTURE.md).

## 1. Overview

```
client → api/ → store/ (SQLite, WAL)
                 ↑   ↓
              workq/ (sqlite | redis)
                 ↓
              worker/ (pool) → governor (pairs) → flow/ → challenge/
                 ↓
              obs/ (Prometheus)
```

The split is deliberate:

* `api/` does not know how a sign-in is performed — it only accepts jobs and
  reports status;
* `worker/` does not know the HTTP exchange — it leases a job, calls `flow/`
  and stores the result;
* `flow/` knows nothing about queues or pools — only the protocol;
* `challenge/` is plugged in behind an interface and can be replaced.

## 2. Modules

### `api/`

| File | Responsibility |
|---|---|
| `main.py` | application assembly, routers, periodic metrics collection |
| `routes.py` | handlers: job intake, status, list, health, metrics |
| `schemas.py` | request and response models (pydantic) |

Job intake returns the result immediately when it appears within
`wait_seconds`; otherwise `202` plus an id to poll. This removes the need for
a separate broker in synchronous scenarios.

### `worker/`

| File | Responsibility |
|---|---|
| `main.py` | pool startup, signal handling, graceful shutdown |
| `pool.py` | worker set with a shared governor and metrics |
| `runner.py` | loop: lease → call `flow/` → persist → pause |
| `governor.py` | per-pair pacing and cooldown growth |
| `metrics_server.py` | separate metrics port |

### `flow/`

| File | Responsibility |
|---|---|
| `client.py` | the full exchange: CSRF handshake, sign-in, session verification |
| `detect.py` | response classification: success, challenge, manual step, rejection |

`detect.py` must never confuse "challenge required" with "manual step": the two
statuses behave differently in the queue.

### `store/`

SQLite in WAL mode, one connection per thread. Sharing a single connection
serialises workers on the write lock: throughput was measured at 391.7 jobs/min
before and 94.8 jobs/min after a regression that reintroduced it.

| Table | Contents |
|---|---|
| `jobs` | jobs: credentials reference, status, attempt, lease, result, error |
| `profiles` | network profiles: endpoint, state, counters |
| `workers` | workers: identifier, heartbeat |
| `pairs` | "account + profile" pairs: last run, cooldown, statistics |
| `meta` | service values |

### `workq/`

| File | Responsibility |
|---|---|
| `base.py` | queue interface |
| `sqlite_q.py` | local queue on the same database |
| `redis_q.py` | queue for multiple hosts |
| `factory.py` | selection via `SPK_QUEUE_BACKEND` |

Leases are time-bounded (`SPK_QUEUE_LEASE_SECONDS`): if a worker dies, the job
returns to the queue instead of disappearing.

### `profiles/`

Profile model and pool. A profile is a network endpoint with state: free,
busy, cooling down, flagged. The pool never uses one endpoint beyond
`SPK_MAX_CONCURRENT_PER_HOST` concurrent jobs.

### `procs/`

Orphan cleanup for browser processes: when a browser provider crashes, the
child process survives and holds memory. The module finds processes whose
parent is gone and terminates them.

### `obs/`

Prometheus metrics plus a store snapshot published as gauges
(`jobs_by_status`, `jobs_in_state`).

## 3. Job lifecycle

1. `POST /api/v1/login` — the job is written to `jobs` as `queued`.
2. A worker leases it, sets `running`, refreshes its heartbeat.
3. The governor checks the pair: if the pause has not elapsed, the job waits
   without consuming an attempt.
4. `flow/` performs the exchange. Possible outcomes:
   * `SUCCESS` — session obtained and verified;
   * `CHALLENGE_FAILED` — the platform requires a challenge;
   * `MANUAL_REQUIRED` — a manual step is needed (two-factor and similar);
   * `INVALID_CREDENTIALS` — wrong pair;
   * `PROXY_ERROR` — network failure, the attempt is not consumed.
5. The result is written to `jobs`, the pair is updated in `pairs`.
6. On failure the job is retried with growing backoff, up to
   `SPK_JOB_ATTEMPT_LIMIT`.

## 4. Risk governor

Its purpose is to avoid burning accounts.

| Rule | Default |
|---|---|
| Minimum interval between attempts of one pair | 180 s |
| Base cooldown after a rejection | 300 s |
| Maximum cooldown | 3600 s |
| Network failure consumes an attempt | no |
| Waiting consumes an attempt | no |

Backoff grows exponentially and resets after a success.

## 5. Scaling

| Bottleneck | Remedy |
|---|---|
| SQLite write lock | WAL once, per-thread connections |
| Single host | `SPK_QUEUE_BACKEND=redis`, workers on several machines |
| Many concurrent sign-ins | `SPK_MAX_CONCURRENT_PER_HOST`, profile pool |
| Browser provider | separate process, `procs/` cleanup |

## 6. Failure points

| Failure | Symptom | Behaviour |
|---|---|---|
| Proxy unreachable | `PROXY_ERROR` | attempt not consumed, profile cooled down |
| Platform changed the protocol | unexpected status code | `CHALLENGE_FAILED`, logged |
| Worker died | lease expired | job returns to the queue |
| Database locked | write timeout | retry with pause |

## 7. Observability

* `/healthz` — active worker count comes from the `workers` table by
  heartbeat (30 s window), not from configuration.
* `/metrics` — exchange duration, attempts by outcome, jobs by status, browser
  traffic.
* Logs — structured (`SPK_LOG_JSON=true`), carrying the job id.
