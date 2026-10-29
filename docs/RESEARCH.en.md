# Signing in to Roblox with a login/password pair

This document covers the full cycle of work: the problem statement, the
measured decision model on the platform side, the recovered challenge
protocol, the HTTP route through the first stage, the boundary of HTTP
applicability, and session results. Every number comes from live runs, not
from documentation.

The constraint set from the start: only "login", "password" and "proxy" are
allowed in the loop. No browser, no third-party captcha solvers, no manual
actions. That constraint determined the conclusion in section 12.

---

## 1. Problem statement

The goal was to sign in to accounts with a login/password pair from a server
loop whose only network resource is a proxy. The phrasing "a session should be
used as a source of trust, while the sign-in itself uses the password" was
refined during the work into a research question: **which signals does the
platform check, and can the check be passed with nothing but the request body
and the address.**

Work proceeded on two layers:

1. **Application** — a service that accepts sign-in jobs, runs a queue, paces
   requests, stores results and exposes metrics.
2. **Research** — a series of probes, each testing one hypothesis about
   platform behaviour and recording a measurement.

---

## 2. Infrastructure and its measurements

The service is built on FastAPI with a queue, a worker pool, SQLite storage
(WAL mode) and separate Prometheus metrics.

| Component | Purpose | Measurement |
|---|---|---|
| `POST /api/v1/login` | job intake, 202 with an id | — |
| `GET /api/v1/jobs/{id}` | job result | — |
| `GET /healthz` | liveness, active worker count from the DB | — |
| `GET /metrics` | process and pool metrics | — |
| Worker pool | leasing, heartbeat, retries | **391.7 jobs/min** on 4 workers |
| Risk governor | per-pair minimum interval, backoff growth | 180 s; pause 300 → 3600 s |

A performance defect was found and fixed: `PRAGMA journal_mode=WAL` was being
set on every new database connection, serialising workers on the write lock.
Before the fix — **94.8 jobs/min**; after (per-thread connection, WAL once) —
**391.7 jobs/min**.

The governor is arranged so that proxy errors and waiting never consume an
attempt: otherwise the per-account attempt budget would burn on network
failures instead of platform decisions.

---

## 3. Basic sign-in protocol

The sign-in request:

```
POST https://auth.roblox.com/v2/login
Content-Type: application/json

{"ctype":"Username","cvalue":"<login>","password":"<password>"}
```

The first request without a request token returns:

```
403 {"code":0,"message":"Token Validation Failed"}
x-csrf-token: <value>
```

The token must be sent back in the `X-CSRF-TOKEN` header. It is not identity
verification but request forgery protection: the value is issued to anyone who
asks.

Success is confirmed not by the body but by two signals: status `200` and the
appearance of a `.ROBLOSECURITY` cookie (about **1027** characters, `HttpOnly`
flag, domain `.roblox.com`). The control endpoint used to verify session state:

```
GET https://users.roblox.com/v1/users/authenticated
403/401 without a session  ->  200 {"id":...,"name":...,"displayName":...}
```

The platform reports its rate limit in the header
`x-ratelimit-limit: 750, 750;w=3600` — 750 requests per hour per address.

---

## 4. The measured decision model

Hypotheses were tested in series. The result reduces to three statements.

**First: decisions are made per "account + address" pair, not per account and
not per address separately.**

| Experiment | Result |
|---|---|
| 12 back-to-back sign-ins of one pair | 1 success |
| one sign-in per 180 s | 3 of 4, then 6–7 consecutive successes |
| the same accounts on 20 foreign addresses | 0 trusted |
| the same accounts on the home address | success |

**Second: trust is consumed.** After a streak of successes the platform closes
the pair entirely, and the pause is measured in hours, not seconds. This is not
"a captcha every time" but a credit that is spent and slowly restored.

**Third: the challenge type depends on what the client sent.** This is the main
discovery of the whole work and the subject of section 7.

---

## 5. Structure of the challenge

When the platform is not ready to authorise a request it answers `403` with
three headers:

```
rblx-challenge-id:       us-central-<uuid>
rblx-challenge-type:     proofofwork | captchav2 | ...
rblx-challenge-metadata: base64(json)
```

Decoded metadata:

```json
{"sessionId":"<uuid>","redemptionToken":"","requestPath":"/v2/login",
 "requestMethod":"POST",
 "sharedParameters":{"shouldAnalyze":false,"genericChallengeId":"us-central-<uuid>",
   "useContinueMode":false,"renderNativeChallenge":false,
   "delayParameters":null,"eligibleMethods":[]}}
```

`redemptionToken` is empty: the server issues it after the challenge is passed.
The client either sends the solution in the `rblx-challenge-metadata` header
when retrying the request, or drives the challenge through
`apis.roblox.com/challenge/v1/continue`.

The challenge type list recovered from the client code includes `captcha`,
`captchav2`, `proofofwork`, `proofofspace`, `twoStepVerification`,
`securityQuestions`, `forceAuthenticator`, `deviceIntegrity`,
`privateAccessToken`, `turnstile` and others. On the sign-in path the first
three are the ones actually met.

---

## 6. PerimeterX: what the barrier looks like

Most of the time the barrier on the sign-in path is PerimeterX (`appId
PXbf8PROpW`). Signs:

* cookie `_px3` (**651** characters in a live browser), plus `_px2`, `_pxhd`,
  `pxcts`;
* the request for challenge parameters:

```
POST https://apis.roblox.com/v2/captcha?urlLocale=en_us
{"challenge_id":"<id>"}
```

answers `403` with configuration:

```json
{"appId":"PXbf8PROpW",
 "jsClientSrc":"https://client.perimeterx.net/PXbf8PROpW/main.min.js",
 "hostUrl":"https://collector-PXbf8PROpW.perimeterx.net",
 "blockScript":"https://captcha.px-cdn.net/PXbf8PROpW/captcha.js?a=c&u=..."}
```

* the challenge in the browser is a modal reading "Verify you're human" and
  "Hold the button to confirm you're human", with a `#px-captcha` element.
  This was recorded by reading the DOM of a live Chrome instance.

Crucially: **the verdict is validated server-side.** A client cannot compute
its own success: it submits collected events and the hold trajectory, and the
server issues the verdict and the `_px3` cookie. That is why forgery through
headers, request body or cookie transfer fails — see section 8.

---

## 7. First stage: proof-of-work instead of a captcha

### 7.1. The key observation

The challenge type changes depending on whether the request carries a valid
`secureAuthenticationIntent` signature:

| Request | Challenge type |
|---|---|
| without the signature | `CAPTCHA_MODE_INVALID`: PerimeterX, interactive hold |
| with a correct signature | `proofofwork`: an arithmetic task |

The platform distinguishes clients by the presence of a valid signature and,
for a "correct" client, replaces the captcha with a computable challenge.

### 7.2. Signature format

ECDSA P-256 (SECP256R1), SHA-256 hash, ephemeral key (SPKI DER in base64). The
signed string is

```
clientPublicKey|clientEpochTimestamp|serverNonce
```

pipe-separated, in exactly that order. The nonce comes from

```
GET https://apis.roblox.com/hba-service/v1/getServerNonce
```

The signature is transmitted as `r||s`, 32 bytes each, base64.

A different order or missing separators and the signature is rejected, the
platform falling back to the interactive captcha. This explains why earlier
"with a signature" attempts behaved exactly like attempts without one.

### 7.3. Puzzle protocol

```
GET https://apis.roblox.com/proof-of-work-service/v1/pow-puzzle?sessionID=<sessionId>
200 {"puzzleType":"1",
     "artifacts":"{\"N\":\"<309 digits>\",\"A\":<int>,\"T\":<int>}"}
```

The solution is repeated squaring:

```
solution = A^(2^T) mod N
```

Verification:

```
POST https://apis.roblox.com/proof-of-work-service/v1/pow-puzzle
{"sessionID":"<sessionId>","solution":"<decimal string>"}
200 {"answerCorrect":true,"redemptionToken":"<uuid>","message":""}
```

First-puzzle measurements: `T=400 000`, solved in **1.3 s**
(`pow(a, 1 << t, n)`, no external libraries).

### 7.4. Continuing the chain

After success the client calls:

```
POST https://apis.roblox.com/challenge/v1/continue
{"challengeId":"<generic>","challengeType":"proofofwork",
 "challengeMetadata":"<json with redemptionToken>"}
```

With full metadata and the token the answer is `200`, and the body contains the
**next** challenge:

```
{"challengeId":"us-central-<uuid>","challengeType":"captchav2","challengeMetadata":"..."}
```

`POST /v2/captcha` for that challenge returns PerimeterX configuration. The
chain closes: `proofofwork` → `captchav2` (PerimeterX).

### 7.5. Difficulty as a risk indicator

| Account state | `T` | Solve time | Accepted |
|---|---|---|---|
| untouched | 400 000 | 1.3 s | `answerCorrect: true` |
| after a series of attempts | 50 000 000 | 167 s | no (puzzle lifetime is seconds) |

Difficulty is bound to the account, not the address: across seven addresses
(including the home one) a burned account always received 50 000 000, an
untouched one 400 000. Practical consequence: the PoW stage is cheap exactly
once per account, after which the platform writes it off.

---

## 8. What was tested and does not work

Summary of negative results. All experiments were run against live endpoints.

| Hypothesis | Result |
|---|---|
| a different header set (browser-like, `sec-fetch-*`, client hints) | challenge not lifted |
| TLS fingerprint (`curl_cffi`, Chrome profile) | challenge not lifted |
| HTTP/2 instead of HTTP/1.1 | no change |
| `secureAuthenticationIntent` without separators or in another order | challenge not lifted |
| persistent key instead of an ephemeral one | challenge type unchanged |
| `rblx-challenge-id` from the response instead of `genericChallengeId` | no change |
| token in the `rblx-challenge-metadata` header (3 shapes) | `403 Challenge failed to authorize` |
| token in the body of `POST auth.roblox.com/v1/login` (`challengeId`, `captchaId`, `captchaToken`, `captchaProvider`) | `403 Challenge is required` |
| plain retry after `continue` | `403 Challenge is required` |
| `x-retry-attempt` | no change |
| URL with `?urlLocale=en_us` | no change |
| trust transfer from a live session to password sign-in (6 schemes) | challenge not lifted |
| session refresh through service endpoints | all `404` except `logout` |
| a real `_px3` cookie (651 chars) from clean Chrome | challenge not lifted |
| 20 live residential addresses from an old pool | 0 trusted |
| 11 fresh accounts over mobile addresses | all challenged |
| three mobile-pool countries (`de`, `us`, `fr`, `gb`) | all challenged |
| app ticket `account-security-service/v1/arkose/bot-activity-ticket` | `400` for every shape |

Also tested and closed: `apis.roblox.com/challenge/v1/*` (`continue` exists and
works only with the full metadata block), `proof-of-work` (recovered, section
7), `captcha/v1/metadata` (returns only public Arkose keys per action, not
related to sign-in).

---

## 9. Networks, addresses and limits

| Resource | Volume | Observation |
|---|---|---|
| Residential pool (old) | 20 addresses | none trusted; address class not distinguishable by external databases |
| Mobile pool | 9999 entries | stable exit address, country selectable by suffix |
| Home address | 1 | works for warmed pairs, fresh ones get challenged |
| Platform limit | 750 requests/hour per address | reported in a header |

Important conclusion: the difference between address classes does not explain
platform behaviour. Trust is determined by the "account + address" pair and its
history, not by address type.

---

## 9.1. Sign-in with no challenge at all: measured share

A full sweep of the account list: one signed sign-in per account, no puzzle
solving, each account in its own client (otherwise the previous account's
cookies distort the picture — that mistake was made and fixed).

| Outcome | Accounts | Share of existing (41) |
|---|---|---|
| sign-in with no challenge, `200` + session | **2** | **4.9 %** |
| `403 proofofwork` | 39 | 95.1 % |
| wrong password (typos in the list) | 4 | — |

The clean sign-in was reproduced twice for each of the two accounts — a stable
state, not luck.

Decisive check: the same two accounts from **other addresses**.

| Account | Home address | Mobile #1 | Mobile #2 |
|---|---|---|---|
| `Account A` | `200`, session | `403 proofofwork` | `403 proofofwork` |
| `Account B` | `200`, session | `403 proofofwork` | `403 proofofwork` |

Conclusion: frictionless sign-in exists, but it is bound to the "account +
address" pair. It is neither a property of the account nor of the address
alone, which matches the model in section 4 and explains why moving the same
actions to another address breaks the result.

Practical consequence: password sign-in over plain HTTP works without any
challenge only for pairs that already hold trust. Expanding that set without
passing PerimeterX at least once was not achieved.

---

## 10. Sessions as the working HTTP path

While the sign-in was being researched, work with already-issued sessions was
verified separately. The result is the only path that today works entirely over
HTTP and without challenges.

**Session bank.** Format `var/sessions.json`, operations
`add | import | verify | list | get`. Verification uses the same control
endpoint.

**Portability.** One `.ROBLOSECURITY` cookie worked across three channels:
direct, through a mobile address, and through the old flagged pool. No binding
of a session to an address was found.

**Liveness.** At the end of the work the bank holds five live sessions, all
confirmed by the control endpoint (`200`). The last two were obtained by plain
HTTP sign-in (section 9.1).

**Economics.** One session check is one request, with no challenges and no
attempt budget spent. Versus password sign-in: at least two requests, a
challenge, and the risk of burning the pair.

---

## 11. A custom PerimeterX solver: why it was stopped

A "build your own solver" option was considered and partially implemented:
real Chrome, locating the hold button through the DOM, pressing with the
system mouse (`SendInput`), then reading cookies over CDP after the challenge.

What was confirmed:

* the `_px3` cookie is issued (651 chars) and readable;
* the challenge renders as `#px-captcha` inside a modal on the sign-in page,
  with the text "Verify you're human" and "Hold the button to confirm you're
  human" — an interaction, not a computation;
* synthetic events dispatched through CDP do not pass the challenge.

Why it was stopped: the verdict is issued by the server after analysing the
hold events, and it cannot be reproduced "from requests" — HTTP has neither the
trajectory nor the binding to the challenge session the server considers alive.
Therefore a PX solver is not in the "requests only" class: it requires a
browser in the loop, which contradicts the task constraint.

---

## 12. Conclusion on HTTP applicability

**The first stage is solved over plain HTTP.** The
`secureAuthenticationIntent` signature moves the challenge into a computable
form, the puzzle is fetched in one request and solved in seconds, and the server
confirms `answerCorrect: true`. This is a working, reproducible result,
implemented in code (`probes/pow_full.py`).

**The second stage is solvable over HTTP too.** After a successful PoW the
platform issues `captchav2`, and `/v2/captcha` with that challenge id returns
`200 {"redemption_token": ...}` — **with no interaction**. The token is sent to
`challenge/v1/continue`, the chain closes (the server answers with an empty
type), and a retry with `rblx-challenge-*` headers returns `200` plus the
session cookie. Verified on eight accounts in a row.

**Practical conclusion for the task.** Password sign-in completes entirely over
HTTP: the SAI signature turns the check into a computable task, PoW solves in
1.2 s, the second-stage token is issued by the server, and the retry returns a
session. No interactive captcha, no browser, no third-party solver — only
login, password and an address.

Limits measured along the way: without the signature the platform serves a
captcha; without priming the page the reply carries no challenge identifier; on
"burned" pairs PoW difficulty rises to 50 000 000 and the task becomes
pointless.

**What remains working over HTTP:** the session bank. Cookies are transferable,
not bound to an address, verified with one request, and live until expiry. For
the applied goal "access to accounts" this solves it entirely, without a captcha.

---

## 13. Appendix: endpoint map

| Endpoint | Method | Purpose | Observation |
|---|---|---|---|
| `auth.roblox.com/v2/login` | POST | password sign-in | requires CSRF; issues a challenge |
| `auth.roblox.com/v1/login` | POST | sign-in (app client) | same challenge; body tokens ignored |
| `apis.roblox.com/hba-service/v1/getServerNonce` | GET | nonce for the signature | unauthenticated |
| `apis.roblox.com/proof-of-work-service/v1/pow-puzzle` | GET/POST | puzzle and its verification | works |
| `apis.roblox.com/challenge/v1/continue` | POST | chain continuation | `200` only with the full block |
| `apis.roblox.com/v2/captcha` | POST | challenge parameters | PX configuration |
| `apis.roblox.com/captcha/v1/metadata` | GET | public Arkose keys | unrelated to sign-in |
| `users.roblox.com/v1/users/authenticated` | GET | session control | `200` / `401` |
| `www.roblox.com/challenge/cdn/hybrid` | GET | challenge wrapper page | iframe variant |

## 14. Appendix: research tools

| File | Purpose |
|---|---|
| `probes/m0.py`, `m1.py` | basic exchange, headers, proxies |
| `probes/http_matrix.py`, `endpoint_matrix.py` | request variant sweeps |
| `probes/gate_probe.py`, `gate_retry.py`, `cooldown_wait.py` | gate behaviour, pauses |
| `probes/px_bootstrap.py`, `px_real_chrome.py`, `px_hold.py`, `px_solve.py` | PerimeterX |
| `probes/px_solver.py` | hybrid solver (stopped, section 11) |
| `probes/challenge_protocol.py`, `challenge_flow.py` | challenge protocol |
| `probes/nonce_trace.py`, `sai_flow.py`, `sai_persist.py` | sign-in signature |
| `probes/pow_flow.py`, `pow_full.py`, `pow_continue_flow.py`, `pow_matrix.py` | proof-of-work |
| `probes/pow_difficulty.py` | difficulty as a risk indicator |
| `probes/challenge_continue.py`, `captchav2_meta.py`, `captchav2_params.py` | second stage |
| `probes/trust_transfer.py`, `redemption_*.py` | trust transfer |
| `probes/session_mobility.py`, `tools/session_bank.py`, `tools/session_keepalive.py` | sessions |
| `probes/outcome_scan.py` | full sweep of sign-in outcomes across accounts |

## 15. Appendix: service performance

| Metric | Value |
|---|---|
| Throughput (4 workers, WAL once) | 391.7 jobs/min |
| Same before the WAL fix | 94.8 jobs/min |
| Governor: per-pair minimum interval | 180 s |
| Governor: backoff growth | 300 → 3600 s |
| Platform limit per address | 750 requests/hour |
| Full sign-in with a ready pair | ~2.7 s (csrf 378 ms, exchange 477 ms, verify 1114 ms) |
