"""Матрица вариантов повторного входа после PoW.

Проверяет комбинации:
  * форма метаданных: только токен / токен+sessionId / полный блок с токеном;
  * идентификатор в заголовке: `rblx-challenge-id` из ответа или
    `genericChallengeId` из общих параметров;
  * заголовок `x-retry-attempt` — есть или нет.

Каждый вариант — свежий challenge (вход, задание, решение, проверка, повтор),
потому что токен одноразовый.

    python -u -m probes.pow_matrix --account <login>
"""

from __future__ import annotations

import argparse
import base64
import json
import time
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)
H = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://www.roblox.com",
    "Referer": "https://www.roblox.com/login",
    "sec-ch-ua": '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
}
LOGIN = "https://auth.roblox.com/v2/login"
PUZZLE = "https://apis.roblox.com/proof-of-work-service/v1/pow-puzzle"


def b64json(v: str) -> dict:
    pad = "=" * (-len(v) % 4)
    for dec in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            return json.loads(dec(v + pad))
        except Exception:
            continue
    return {}


def b64(d: dict) -> str:
    return base64.b64encode(json.dumps(d).encode()).decode()


def sai(nonce: str) -> dict:
    key = ec.generate_private_key(ec.SECP256R1())
    spki = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    pub = base64.b64encode(spki).decode()
    ts = int(time.time())
    der = key.sign(f"{pub}|{ts}|{nonce}".encode(), ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der)
    return {"clientPublicKey": pub, "clientEpochTimestamp": ts, "serverNonce": nonce,
            "saiSignature": base64.b64encode(
                r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode()}


def attempt(client: httpx.Client, account: dict, shape: str, id_kind: str,
            retry_header: bool) -> dict:
    r0 = client.post(LOGIN, headers=H,
                     json={"ctype": "Username", "cvalue": "probe_zz", "password": "x"})
    headers = dict(H)
    if r0.headers.get("x-csrf-token"):
        headers["X-CSRF-TOKEN"] = r0.headers["x-csrf-token"]
    nonce = client.get("https://apis.roblox.com/hba-service/v1/getServerNonce",
                       headers={"User-Agent": UA, "Accept": "application/json"}).json()
    body = {"ctype": "Username", "cvalue": account["login"],
            "password": account["password"], "secureAuthenticationIntent": sai(str(nonce))}
    r1 = client.post(LOGIN, headers=headers, json=body)
    # Токен мог обновиться этим запросом — берём самый свежий.
    fresh_csrf = r1.headers.get("x-csrf-token")
    if fresh_csrf:
        headers["X-CSRF-TOKEN"] = fresh_csrf
    meta = b64json(r1.headers.get("rblx-challenge-metadata", ""))
    ch_id = r1.headers.get("rblx-challenge-id", "")
    ch_type = r1.headers.get("rblx-challenge-type", "")
    sid = meta.get("sessionId")
    generic = (meta.get("sharedParameters") or {}).get("genericChallengeId")
    if not sid:
        return {"skip": f"{r1.status_code} {r1.text[:90]}"}

    p = client.get(PUZZLE, headers={"User-Agent": UA, "Accept": "application/json"},
                   params={"sessionID": sid}).json()
    artifacts = p.get("artifacts")
    if isinstance(artifacts, str):
        artifacts = json.loads(artifacts)
    solution = str(pow(int(artifacts["A"]), 1 << int(artifacts["T"]), int(artifacts["N"])))
    v = client.post(PUZZLE, headers=headers,
                    json={"sessionID": sid, "solution": solution}).json()
    if not v.get("redemptionToken"):
        # Токен запроса мог смениться: берём новый и пробуем ещё раз.
        r_probe = client.post(LOGIN, headers=H,
                              json={"ctype": "Username", "cvalue": "probe_zz", "password": "x"})
        new_token = r_probe.headers.get("x-csrf-token")
        if new_token:
            headers["X-CSRF-TOKEN"] = new_token
            v = client.post(PUZZLE, headers=headers,
                            json={"sessionID": sid, "solution": solution}).json()
    token = v.get("redemptionToken")
    if not token:
        return {"skip": f"нет токена: {v}"}

    if shape == "token":
        md = {"redemptionToken": token}
    elif shape == "both":
        md = {"sessionId": sid, "redemptionToken": token}
    else:
        md = dict(meta)
        md["redemptionToken"] = token

    h2 = dict(headers)
    h2["rblx-challenge-id"] = (generic if id_kind == "generic" else ch_id) or sid
    h2["rblx-challenge-type"] = ch_type or "proofofwork"
    h2["rblx-challenge-metadata"] = b64(md)
    if retry_header:
        h2["x-retry-attempt"] = "1"
    r2 = client.post(LOGIN, headers=h2, json=body)
    jars = {c.name: c.value for c in client.cookies.jar}
    return {"status": r2.status_code, "body": r2.text[:160],
            "session": ".ROBLOSECURITY" in jars, "jar": sorted(jars),
            "shape": shape, "id": id_kind, "retry_header": retry_header}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="<login>")
    ap.add_argument("--out", default="var/pow-matrix.json")
    args = ap.parse_args()

    accounts = json.loads(Path("var/accounts.json").read_text(encoding="utf-8"))
    account = next(a for a in accounts if a["login"].lower() == args.account.lower())
    print(f"аккаунт: {account['login']}", flush=True)

    results = []
    combos = [(s, i, r) for s in ("token", "both", "patched")
              for i in ("challenge_id", "generic") for r in (True, False)]

    with httpx.Client(timeout=60, follow_redirects=False) as client:
        client.get("https://www.roblox.com/login", headers={**H, "Accept": "text/html"})
        for n, (shape, id_kind, retry_header) in enumerate(combos, 1):
            try:
                res = attempt(client, account, shape, id_kind, retry_header)
            except Exception as exc:
                res = {"error": f"{type(exc).__name__}: {str(exc)[:90]}"}
            res.update({"shape": shape, "id": id_kind, "retry_header": retry_header})
            results.append(res)
            verdict = res.get("status", res.get("skip", res.get("error")))
            print(f"  {n:>2}/12 {shape:<8} {id_kind:<13} retry={str(retry_header):<5} "
                  f"-> {verdict} {str(res.get('body', ''))[:80]!r}", flush=True)
            if res.get("session"):
                print("     СЕССИЯ ПОЛУЧЕНА", flush=True)
                (Path("var") / f"session-{account['login']}.json").write_text(
                    json.dumps({c.name: c.value for c in client.cookies.jar},
                               ensure_ascii=False, indent=2), encoding="utf-8")
                break
            time.sleep(1.5)

    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = [r for r in results if r.get("session")]
    print(f"\nуспешных: {len(ok)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
