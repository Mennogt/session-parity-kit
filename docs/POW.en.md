# Proof-of-work instead of a captcha — the protocol

Companion to the Russian original: [POW.md](POW.md). Full research:
[RESEARCH.en.md](RESEARCH.en.md).

## The key observation

The challenge type depends on whether the sign-in request carries a valid
`secureAuthenticationIntent` signature.

| Request | Challenge type |
|---|---|
| without the signature | `CAPTCHA_MODE_INVALID`: PerimeterX, interactive hold |
| **with a correct signature** | **`proofofwork`: pure arithmetic** |

## The signature

* Curve: ECDSA P-256 (SECP256R1), hash SHA-256.
* Message: `clientPublicKey|clientEpochTimestamp|serverNonce` — **pipe-separated**,
  in exactly that order.
* `clientPublicKey` — SPKI DER in base64, ephemeral key (fresh per attempt).
* `serverNonce` — from `GET https://apis.roblox.com/hba-service/v1/getServerNonce`.
* `saiSignature` — raw `r||s`, 32 bytes each, base64.

A different order or missing separators and the platform rejects the signature,
falling back to the interactive captcha.

## The PoW protocol

```
GET  https://apis.roblox.com/proof-of-work-service/v1/pow-puzzle?sessionID=<sessionId>
->   200
     {"puzzleType":"1",
      "artifacts":"{\"N\":\"<309 digits>\",\"A\":<int>,\"T\":400000}"}

solution = A^(2^T) mod N        // repeated squaring

POST https://apis.roblox.com/proof-of-work-service/v1/pow-puzzle
     {"sessionID":"<sessionId>","solution":"<solution>"}
->   200 {"answerCorrect":true,"redemptionToken":"<uuid>","message":""}
```

`solution` is the decimal string of the result.

## Measurements

| Step | Result |
|---|---|
| Sign-in with signature | 403 + `rblx-challenge-type: proofofwork` + metadata |
| Fetch the puzzle | 200, one request |
| Solve | **1.3 s** (`pow(a, 1 << t, n)` at C speed) |
| Verify | `answerCorrect: true`, token issued |

No browser, no third-party solver, no special proxy at this stage.

## The chain does not end there

After the PoW token, the client calls:

```
POST https://apis.roblox.com/challenge/v1/continue
{"challengeId":"<generic>","challengeType":"proofofwork",
 "challengeMetadata":"<json with redemptionToken>"}
```

With the full metadata and token the response is `200`, and it carries the
**next** challenge:

```
{"challengeId":"us-central-<uuid>","challengeType":"captchav2", ...}
```

`POST /v2/captcha` for that challenge returns PerimeterX configuration — so the
chain is `proofofwork` → `captchav2` (PerimeterX).

## Difficulty is a risk signal

| Account state | `T` | Solve time | Accepted |
|---|---|---|---|
| untouched | 400 000 | 1.3 s | yes (`answerCorrect: true`) |
| after a series of attempts | 50 000 000 | 167 s | no (puzzle lifetime is seconds) |

Difficulty is bound to the account, not the address: across seven different
addresses (including the home one) a burned account always received
50 000 000, while an untouched one received 400 000. Practically, the PoW stage
is cheap exactly once per account.

## What was recovered from

* `var/js/*.js` (client bundles) — the generic challenge service, type
  schemas (`proofofwork` → `{sessionId}`), handler mapping;
* `var/pow-worker.js` — the `ChallengeWebWorkers` worker with the
  `TIME_LOCK_PUZZLE` and `GRAPH_PEBBLING_PUZZLE` solvers;
* live requests: `probes/pow_full.py`, `probes/challenge_continue.py`.

## What is left

The second stage, PerimeterX. It is not solvable from requests alone — or so
it seemed. See below.

---

# The second stage is solved: a full captcha-free flow

The second stage does not actually require interaction if the protocol is
followed to the end. The server issues the token itself.

## Protocol

```
1. POST auth.roblox.com/v2/login + SAI signature
   -> 403 rblx-challenge-type: proofofwork

2. GET  .../pow-puzzle?sessionID=...
   -> N, A, T;   solution = A^(2^T) mod N

3. POST .../pow-puzzle {"sessionID","solution"}
   -> {"answerCorrect":true,"redemptionToken":"<uuid>"}

4. POST .../challenge/v1/continue
   {"challengeId":"<generic>","challengeType":"proofofwork",
    "challengeMetadata":"<json with redemptionToken>"}
   -> 200 {"challengeId":"us-central-...","challengeType":"captchav2",...}

5. POST apis.roblox.com/v2/captcha?urlLocale=en_us
   {"challenge_id":"<second stage id>"}
   -> 200 {"redemption_token":"<uuid>"}          <- no interaction

6. POST .../challenge/v1/continue
   {"challengeId":"<second stage id>","challengeType":"captchav2",
    "challengeMetadata":"{\"redemptionToken\":\"<uuid>\"}"}
   -> 200 {"challengeId":"...","challengeType":"","challengeMetadata":""}

7. POST auth.roblox.com/v2/login + same body and signature, headers:
   rblx-challenge-id: <second stage id>
   rblx-challenge-type: captchav2
   rblx-challenge-metadata: base64({"redemptionToken":"<uuid>"})
   -> 200 {"user":{...},"isBanned":false,...} + Set-Cookie .ROBLOSECURITY
```

## Measurements

| Metric | Value |
|---|---|
| PoW solve | 1.2 s |
| Full sign-in | 4.0–5.5 s |
| Interactive steps | 0 |
| Browser | not used |
| Consecutive successes | 8 accounts, 8 live sessions |

## Conditions

| Condition | Why it matters |
|---|---|
| SAI signature in the exchange | without it the platform serves a captcha instead of a puzzle |
| Sign-in page priming | otherwise the reply carries no challenge identifier |
| Client hints (`sec-ch-ua`, `sec-fetch-*`) | with them the reply contains the challenge headers |
| Reasonable `T` | burned pairs get `T = 50 000 000`, which cannot be solved |

## Implementation

| File | Role |
|---|---|
| `flow/sai.py` | builds the sign-in signature |
| `flow/http_challenge.py` | runs the whole chain and retries the exchange |
| `flow/client.py` | calls the chain before the browser provider |
| `probes/no_captcha_batch.py` | batch verification of the path |

Service check: `python -m tools.try_flow --account <login> --proxy-url <url>`
returns `SUCCESS` with a verified session in about 8 s on a fresh address.

