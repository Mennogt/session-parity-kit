"""Подбор формы `rblx-challenge-metadata` при повторе после PoW.

По коду клиента при завершении проверки в заголовок кладётся
`btoa(JSON.stringify(<metadata компонента>))`, а не исходный блок целиком.
Для PoW компонент возвращает результат проверки, поэтому пробуем разные
формы и смотрим, какая принимается.

    python -m probes.pow_retry --account <login>
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


def one_round(client: httpx.Client, account: dict, variant: str) -> dict:
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
    meta = b64json(r1.headers.get("rblx-challenge-metadata", ""))
    ch_id = r1.headers.get("rblx-challenge-id", "")
    ch_type = r1.headers.get("rblx-challenge-type", "")
    sid = meta.get("sessionId")
    if not sid:
        return {"variant": variant, "skip": f"{r1.status_code} {r1.text[:80]}"}

    p = client.get(PUZZLE, headers={"User-Agent": UA, "Accept": "application/json"},
                   params={"sessionID": sid}).json()
    artifacts = p.get("artifacts")
    if isinstance(artifacts, str):
        artifacts = json.loads(artifacts)
    solution = str(pow(int(artifacts["A"]), 1 << int(artifacts["T"]), int(artifacts["N"])))
    v = client.post(PUZZLE, headers={"User-Agent": UA, "Accept": "application/json",
                                     "Content-Type": "application/json"},
                    json={"sessionID": sid, "solution": solution}).json()
    token = v.get("redemptionToken")
    if not token:
        return {"variant": variant, "skip": f"нет токена: {v}"}

    if variant == "token_only":
        md = {"redemptionToken": token}
    elif variant == "token_sid":
        md = {"sessionId": sid, "redemptionToken": token}
    elif variant == "full_patch":
        md = dict(meta); md["redemptionToken"] = token
    elif variant == "challenge_id":
        md = {"challengeId": ch_id, "redemptionToken": token}
    else:
        md = {"redemptionToken": token}

    headers2 = dict(headers)
    headers2.update({"rblx-challenge-id": ch_id or sid,
                     "rblx-challenge-type": ch_type or "proofofwork",
                     "rblx-challenge-metadata": b64(md)})
    r2 = client.post(LOGIN, headers=headers2, json=body)
    jars = {c.name: c.value for c in client.cookies.jar}
    return {"variant": variant, "status": r2.status_code, "body": r2.text[:200],
            "cookies": sorted(jars), "session": ".ROBLOSECURITY" in jars,
            "metadata_sent": md}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="<login>")
    ap.add_argument("--out", default="var/pow-retry.json")
    args = ap.parse_args()

    accounts = json.loads(Path("var/accounts.json").read_text(encoding="utf-8"))
    account = next(a for a in accounts if a["login"].lower() == args.account.lower())

    results = []
    with httpx.Client(timeout=90, follow_redirects=False) as client:
        client.get("https://www.roblox.com/login", headers={**H, "Accept": "text/html"})
        for variant in ("token_only", "token_sid", "full_patch", "challenge_id"):
            res = one_round(client, account, variant)
            results.append(res)
            print(f"  {variant:<14} -> {res.get('status', res.get('skip'))} "
                  f"{res.get('body', '')[:110]!r}")
            if res.get("session"):
                print("     СЕССИЯ ПОЛУЧЕНА, форма:", variant)
                Path("var").joinpath(f"session-{account['login']}.json").write_text(
                    json.dumps(client.cookies.jar and
                               {c.name: c.value for c in client.cookies.jar},
                               ensure_ascii=False, indent=2), encoding="utf-8")
                break
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
