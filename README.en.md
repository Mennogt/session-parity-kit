# Session Parity Kit

[![License](https://img.shields.io/badge/license-MIT-2f81f7)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-1a7f37)](requirements.txt)
[![Tests](https://img.shields.io/badge/tests-20%20passed-1a7f37)](tests/)
[![Secrets](https://img.shields.io/badge/secrets-0%20found-1a7f37)](docs/SECURITY.md)
[![Русский](https://img.shields.io/badge/read-%D0%A0%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9-656d76)](README.md)

A toolkit for working with Roblox sessions and sign-ins: a queue-backed
service with a worker pool and a risk governor, plus a set of probes for
studying platform behaviour. Everything the project does is plain HTTP.

The repository contains two distinct things:

1. **The service** — job intake, queue, worker pool, storage, metrics, and a
   session bank.
2. **The research** — 60+ probes, each testing one hypothesis about the
   platform and recording a measurement. Findings are in `docs/`.

---

## Contents

- [What has been proven](#what-has-been-proven)
- [Scope and limits](#scope-and-limits)
- [Quick start](#quick-start)
- [Architecture](#architecture)
- [API](#api)
- [Metrics](#metrics)
- [Configuration](#configuration)
- [Tools](#tools)
- [Documentation](#documentation)
- [Security](#security)
- [Limitations and intended use](#limitations-and-intended-use)
- [License](#license)

---

## What has been proven

Every number comes from live requests; methodology and refuted hypotheses are
in [RESEARCH.en.md](docs/RESEARCH.en.md).

| Claim | Measurement |
|---|---|
| Decisions are keyed on the pair "account + address" | 12 back-to-back sign-ins → 1 success; one sign-in per 180 s → 3 of 4, then a streak, then a hard block |
| Trust is consumed and recovers over hours | cooldown 300 → 3600 s |
| Signing in over plain HTTP with no challenge is possible | **2 of 41 accounts (4.9 %)**, reproduced twice |
| That sign-in is bound to the address | the same accounts from two other addresses → `403 proofofwork` |
| The `secureAuthenticationIntent` signature changes the challenge type | without it PerimeterX, with it `proofofwork` |
| Proof-of-work is solvable over plain HTTP | `T=400 000` in **1.3 s**, `answerCorrect: true` |
| PoW difficulty follows account risk | `T` 400 000 → 50 000 000 (167 s, not accepted) |
| The second stage is PerimeterX | `captchav2` → `/v2/captcha` returns PX configuration |
| **The second stage falls without a captcha** | `/v2/captcha` issues a `redemption_token` with no interaction, `continue` closes the chain |
| **Full sign-in over plain HTTP** | **8 of 8 accounts**, 4.0–5.5 s, no browser |
| Sessions are not bound to an address | one cookie works from three channels |
| Service throughput | **391.7 jobs/min** on 4 workers |

Short version: password sign-in now completes **entirely over HTTP**. The
platform serves a computable proof-of-work task and hands out the second-stage
token itself, so no interactive captcha is involved. The route is implemented
in the service (`flow/sai.py`, `flow/http_challenge.py`) and confirmed with
live sessions.

![Challenge chain](docs/images/chain.svg)

## The picture in four charts

![Sign-in outcomes](docs/images/outcomes.svg)

![PoW difficulty](docs/images/pow-difficulty.svg)

![Throughput](docs/images/throughput.svg)

How to read them:

* **outcomes** — a full sweep of 41 accounts: two sign in with no challenge, 39 get PoW;
* **difficulty** — `T` grows with account risk, and at 50 000 000 solving is pointless;
* **throughput** — the price of one `PRAGMA` placed in the wrong spot.

## How the check is structured

```mermaid
sequenceDiagram
    participant C as Client
    participant A as auth.roblox.com
    participant H as hba-service
    participant P as proof-of-work-service
    C->>A: POST /v2/login (no token)
    A-->>C: 403 + x-csrf-token
    C->>H: GET /v1/getServerNonce
    H-->>C: nonce
    C->>A: POST /v2/login + SAI signature
    A-->>C: 403 rblx-challenge-type: proofofwork
    C->>P: GET /v1/pow-puzzle?sessionID=...
    P-->>C: N, A, T
    Note over C: solve A^(2^T) mod N
    C->>P: POST /v1/pow-puzzle
    P-->>C: answerCorrect + redemptionToken
    C->>A: POST challenge/v1/continue
    A-->>C: 200 → captchav2 (PerimeterX)
    Note over C,A: the HTTP path ends here
```

## Scope and limits

| Scenario | Works |
|---|---|
| Verifying and storing existing sessions | yes, entirely over HTTP |
| Password sign-in for a trusted pair | yes, with no challenge |
| **Password sign-in for the rest** | **yes: 1.2 s PoW plus the second-stage token, no captcha** |
| Defeating PerimeterX from requests alone | not needed: the server issues the token, no interaction required |

---

## Dashboard

One command starts everything and opens the browser:

```bash
python -m tools.launch
```

![Dashboard](docs/images/ui.png)

What it gives you:

* sign-in by login and password — the live HTTP path with no captcha;
* **bulk import**: a file or text of `login:password`, a proxy list, batch runs;
* session bank: state, one-click verification, cookie copy;
* queue and recent jobs with statuses and steps;
* live counters for workers, queue depth and successful sign-ins.

The importer accepts `login:password`, `login;password` and
`login<TAB>password`; `#` lines are skipped and the password may contain a
colon. Re-importing updates the password instead of creating a duplicate.
Proxies may be `http://user:pass@host:port`, `socks5://...` or plain
`host:port` — the default scheme is `http`.

The dashboard binds to `127.0.0.1` and needs no build step or extra
dependencies.

## Quick start

Python 3.11+ (tested on 3.14), Redis optional.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/macOS

pip install -r requirements.txt

copy .env.example .env          # Windows
# cp .env.example .env          # Linux/macOS
```

Run the API and the workers in two terminals:

```bash
python -m uvicorn api.main:app --host 127.0.0.1 --port 8080
python -m worker.main
```

Smoke check:

```bash
curl http://127.0.0.1:8080/healthz
curl http://127.0.0.1:8080/metrics | head
```

Submit a job:

```bash
curl -X POST http://127.0.0.1:8080/api/v1/login \
  -H "Content-Type: application/json" \
  -d '{"login":"user","password":"secret","wait_seconds":30}'
```

Tests:

```bash
python -m pytest -q
```

Docker, if available:

```bash
docker compose up --build
```

---

## Architecture

```
                 POST /api/v1/login
                        │
                  ┌─────▼─────┐        ┌──────────────┐
                  │   api/    │───────▶│   store/     │  SQLite (WAL)
                  │ FastAPI   │        │ jobs,        │  jobs, profiles,
                  └─────┬─────┘        │ profiles,    │  workers, pairs
                        │              │ workers,     │
                        │              │ pairs        │
                  ┌─────▼─────┐        └──────▲───────┘
                  │  workq/   │               │
                  │ sqlite /  │               │
                  │  redis    │               │
                  └─────┬─────┘               │
                        │  lease, heartbeat   │
                  ┌─────▼─────┐        ┌──────┴───────┐
                  │ worker/   │───────▶│  governor    │  per-pair pacing,
                  │ pool      │        │  (pairs)     │  cooldown growth
                  └─────┬─────┘        └──────────────┘
                        │
                  ┌─────▼──────┐       ┌──────────────┐
                  │  flow/     │──────▶│ challenge/   │
                  │ exchange,  │       │ providers    │
                  │ verify     │       └──────────────┘
                  └─────┬──────┘
                        │
                  ┌─────▼──────┐
                  │  obs/      │  Prometheus
                  └────────────┘
```

| Directory | Purpose |
|---|---|
| `api/` | HTTP interface: job intake, status, health, metrics |
| `worker/` | worker pool, leasing, heartbeat, retries, risk governor |
| `flow/` | the exchange itself: CSRF handshake, sign-in, response classification, session verification |
| `challenge/` | challenge providers (currently `playwright`; extensible interface) |
| `store/` | SQLite: jobs, profiles, workers, account+profile pairs |
| `workq/` | queue transport: `sqlite` (single host) or `redis` (multi-host) |
| `profiles/` | network profile model and pool |
| `procs/` | cleanup of orphaned browser processes |
| `obs/` | Prometheus metrics |
| `tools/` | session bank, session keepalive, profile builder |
| `probes/` | platform research probes |
| `bench/` | load-testing CLI |
| `docs/` | findings, protocols, architecture, API |
| `tests/` | unit tests |

---

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/login` | enqueue a sign-in job |
| `GET` | `/api/v1/jobs/{id}` | job status and result |
| `GET` | `/api/v1/jobs` | recent jobs |
| `GET` | `/healthz` | liveness, active worker count |
| `GET` | `/metrics` | Prometheus metrics |

Example response:

```json
{"job_id": "3f1c...", "status": "queued", "result": null}
```

If the job finishes within `wait_seconds`, the response carries the result
immediately; otherwise `202` plus a `job_id` to poll.

Details: [docs/API.md](docs/API.md) (Russian), [docs/API.en.md](docs/API.en.md) (English).

---

## Metrics

| Metric | Type | Meaning |
|---|---|---|
| `login_flow_seconds` | histogram | exchange duration |
| `login_attempts_total` | counter | attempts by outcome |
| `jobs_by_status` | gauge | jobs by status |
| `jobs_in_state` | gauge | jobs in queue states |
| `browser_bytes_total` | counter | browser-provider traffic |
| `browser_sessions_total` | counter | browser sessions started |

Worker metrics are served on a separate port (`SPK_METRICS_PORT`, default
`9090`) because counters live in the process that computes them.

---

## Configuration

The full list is in `.env.example`. Main groups:

| Group | Variables |
|---|---|
| HTTP | `SPK_API_HOST`, `SPK_API_PORT`, `SPK_METRICS_PORT` |
| Storage | `SPK_STORE_PATH` |
| Queue | `SPK_QUEUE_BACKEND`, `SPK_REDIS_URL`, `SPK_QUEUE_LEASE_SECONDS` |
| Pool | `SPK_WORKER_COUNT`, `SPK_JOB_ATTEMPT_LIMIT` |
| Timeouts | `SPK_HTTP_CONNECT_TIMEOUT`, `SPK_HTTP_READ_TIMEOUT`, `SPK_CHALLENGE_TIMEOUT` |
| Governor | `SPK_PAIR_MIN_INTERVAL`, `SPK_PAIR_COOLDOWN_BASE`, `SPK_PAIR_COOLDOWN_MAX` |
| Logs | `SPK_LOG_LEVEL`, `SPK_LOG_JSON`, `SPK_CAPTURE_FIXTURES` |

Secrets never live in code: only in `.env`, which is git-ignored.

---

## Tools

### Session bank

```bash
python -m tools.session_bank add --login user --cookie '<value>'
python -m tools.session_bank verify
python -m tools.session_bank list
python -m tools.session_bank get --login user
```

### Session keepalive

```bash
python -m tools.session_keepalive --once
python -m tools.session_keepalive --interval 3600
```

### Probes (selected)

| File | What it tests |
|---|---|
| `probes/m0.py`, `probes/m1.py` | basic exchange, headers, proxies |
| `probes/outcome_scan.py` | full sweep of sign-in outcomes across accounts |
| `probes/pow_full.py` | complete proof-of-work flow over plain HTTP |
| `probes/pow_difficulty.py` | PoW difficulty as a risk indicator |
| `probes/challenge_protocol.py` | structure of the challenge headers |
| `probes/captchav2_params.py` | second-stage parameters |
| `probes/px_real_chrome.py` | PerimeterX behaviour in real Chrome |
| `probes/trust_transfer.py` | whether trust transfers from session to sign-in |
| `probes/session_mobility.py` | whether a session works from other addresses |

Full list: [docs/RESEARCH.en.md](docs/RESEARCH.en.md), section 14.

---

## Documentation

| Document | Russian | English |
|---|---|---|
| Full research | [RESEARCH.md](docs/RESEARCH.md) | [RESEARCH.en.md](docs/RESEARCH.en.md) |
| Proof-of-work protocol | [POW.md](docs/POW.md) | [POW.en.md](docs/POW.en.md) |
| Architecture | [ARCHITECTURE.md](docs/ARCHITECTURE.md) | [ARCHITECTURE.en.md](docs/ARCHITECTURE.en.md) |
| API | [API.md](docs/API.md) | [API.en.md](docs/API.en.md) |
| Fact log | [FINDINGS.md](docs/FINDINGS.md) | — |
| Refuted hypotheses | [GATE.md](docs/GATE.md) | — |
| Performance | [PERF.md](docs/PERF.md) | — |
| Traffic cost | [COST.md](docs/COST.md) | — |
| Security | [SECURITY.md](docs/SECURITY.md) | included |

---

## Security

* `var/` is fully git-ignored; only `var/README.md` and `var/templates/` are
  published.
* `*.pem`, `*.key`, `.env`, databases and logs are ignored.
* Sign-in cookies, credential pairs and proxy endpoints **never** appear in
  documentation or commits.
* Account names in documents are replaced with placeholders (`Account A`,
  `Account B`).
* Before publishing, scan history: `git log -p | findstr /i "ROBLOSECURITY"`.

Details: [docs/SECURITY.md](docs/SECURITY.md).

---

## Limitations and intended use

The project is meant for use **with your own accounts and your own
infrastructure only**: verifying your sessions, measuring platform behaviour,
assessing your own service's resilience.

There are no third-party credentials in this repository and there must never
be. Lists of foreign pairs found in working directories were not included.

Automating sign-in may violate platform rules. Responsibility for use rests
with whoever runs the code.

---

## License

MIT — see [LICENSE](LICENSE).
