# API

HTTP interface of the service. All responses are JSON. Russian version:
[API.md](API.md).

Default base URL: `http://127.0.0.1:8080`.

## Contents

- [POST /api/v1/login](#post-apiv1login)
- [GET /api/v1/jobs/{id}](#get-apiv1jobsid)
- [GET /api/v1/jobs](#get-apiv1jobs)
- [GET /healthz](#get-healthz)
- [GET /metrics](#get-metrics)
- [Result statuses](#result-statuses)
- [Execution steps](#execution-steps)
- [Errors](#errors)

---

## POST /api/v1/login

Enqueues a sign-in job.

### Request body

| Field | Type | Required | Constraints | Meaning |
|---|---|---|---|---|
| `login` | string | yes | 1…128 chars | username |
| `password` | string | yes | 1…256 chars | password |
| `job_id` | string | no | ≤ 64 chars | caller-supplied id for idempotency |
| `wait_seconds` | number | no | 0…120, default 0 | how long to wait for the result in this request |

```bash
curl -X POST http://127.0.0.1:8080/api/v1/login \
  -H "Content-Type: application/json" \
  -d '{"login":"demo","password":"secret","wait_seconds":30}'
```

### Response

Always contains `job_id` and `state`:

| `state` | When |
|---|---|
| `QUEUED` | accepted, waiting for a worker |
| `RUNNING` | a worker is executing it |
| `DONE` | result ready (only with `wait_seconds > 0`) |

```json
{"job_id": "9c1f4f0e", "state": "DONE", "status": "SUCCESS", "detail": ""}
```

If `wait_seconds` expires before the result appears, the response is `QUEUED`
or `RUNNING`; poll `GET /api/v1/jobs/{id}` afterwards.

---

## GET /api/v1/jobs/{id}

Returns the full job state.

```bash
curl http://127.0.0.1:8080/api/v1/jobs/9c1f4f0e
```

### Response

| Field | Type | Meaning |
|---|---|---|
| `job_id` | string | identifier |
| `login` | string | username |
| `state` | `QUEUED` / `RUNNING` / `DONE` | queue state |
| `status` | string or `null` | outcome, see the status table |
| `step` | string or `null` | where execution stopped |
| `detail` | string or `null` | explanation (platform code, reason) |
| `cookies` | array | obtained cookies (name, value, domain, path, `httpOnly`) |
| `challenge_passed` | boolean | whether a challenge was passed |
| `attempts` | number | attempts consumed |
| `elapsed_ms` | number or `null` | duration in milliseconds |
| `retry_after` | number or `null` | suggested pause in seconds |

Successful job example:

```json
{
  "job_id": "9c1f4f0e",
  "login": "demo",
  "state": "DONE",
  "status": "SUCCESS",
  "step": "verify",
  "detail": "",
  "cookies": [
    {"name": ".ROBLOSECURITY", "value": "<redacted>", "domain": ".roblox.com",
     "path": "/", "httpOnly": true}
  ],
  "challenge_passed": false,
  "attempts": 1,
  "elapsed_ms": 2684,
  "retry_after": null
}
```

Note: cookie values in responses are live secrets. The API binds to
`127.0.0.1` by default; never expose it without authentication.

---

## GET /api/v1/jobs

Recent jobs.

| Query | Type | Default | Meaning |
|---|---|---|---|
| `limit` | integer | 50 | how many to return |

```bash
curl "http://127.0.0.1:8080/api/v1/jobs?limit=10"
```

---

## GET /healthz

Liveness and pool state.

```json
{
  "ok": true,
  "queue_depth": 12,
  "workers": 4,
  "profiles": {"free": 8, "busy": 2, "cooldown": 3, "flagged": 0},
  "stats": {"SUCCESS": 120, "CHALLENGE_FAILED": 40},
  "version": "1.0.0"
}
```

`workers` comes from the worker table by heartbeat (30 s window), not from
configuration, so dead processes are not counted.

---

## GET /metrics

Prometheus metrics.

| Metric | Type | Meaning |
|---|---|---|
| `login_flow_seconds` | histogram | exchange duration |
| `login_attempts_total` | counter | attempts by outcome |
| `jobs_by_status` | gauge | jobs by status |
| `jobs_in_state` | gauge | jobs by queue state |
| `browser_bytes_total` | counter | browser provider traffic |
| `browser_sessions_total` | counter | browser sessions started |

Worker metrics are served on a separate port (`SPK_METRICS_PORT`, default
`9090`).

---

## Result statuses

| Status | Meaning | Retried |
|---|---|---|
| `SUCCESS` | session obtained and verified | no, terminal |
| `BAD_CREDENTIALS` | wrong pair | no, terminal |
| `MANUAL_REQUIRED` | manual step needed (2FA and similar) | no, terminal |
| `CHALLENGE_FAILED` | platform requires a challenge | yes |
| `PROXY_ERROR` | profile network failure | yes, attempt not consumed |
| `RATE_LIMITED` | rate limit hit | yes |
| `INVALID_SESSION` | session did not verify | yes |
| `INTERNAL_ERROR` | internal failure | yes |

---

## Execution steps

| Step | What happens |
|---|---|
| `csrf` | request token negotiation |
| `exchange` | the sign-in itself |
| `challenge` | passing a challenge |
| `retry` | retry after the challenge |
| `verify` | confirming the session with a control request |

`step` in the response shows where the job stopped, which speeds up failure
analysis.

---

## Errors

| Code | Cause | Body |
|---|---|---|
| `400` | body failed validation | `{"detail": "..."}` |
| `404` | job not found | `{"detail": "job not found"}` |
| `422` | wrong field types | default pydantic response |
| `500` | internal error | `{"detail": "internal error"}` |

Validation example:

```bash
curl -i -X POST http://127.0.0.1:8080/api/v1/login \
  -H "Content-Type: application/json" -d '{"login":"","password":""}'
```
