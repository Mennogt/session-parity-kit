"""Схема эндпоинта продолжения проверки.

`apis.roblox.com/challenge/v1/continue` отвечает 500, а не 404 — значит
маршрут существует и ждёт определённые поля. Подбираем схему на живом
challenge, полученном после входа с правильной подписью.

    python -m probes.challenge_continue --account <login>
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
CONTINUE = "https://apis.roblox.com/challenge/v1/continue"


def b64json(value: str) -> dict:
    pad = "=" * (-len(value) % 4)
    for dec in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            return json.loads(dec(value + pad))
        except Exception:
            continue
    return {}


def make_sai(nonce: str) -> dict:
    key = ec.generate_private_key(ec.SECP256R1())
    spki = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    pub = base64.b64encode(spki).decode()
    ts = int(time.time())
    der = key.sign(f"{pub}|{ts}|{nonce}".encode(), ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der)
    sig = base64.b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode()
    return {"clientPublicKey": pub, "clientEpochTimestamp": ts,
            "serverNonce": nonce, "saiSignature": sig}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="<login>")
    ap.add_argument("--proxy-url", default=None)
    ap.add_argument("--out", default="var/challenge-continue.json")
    args = ap.parse_args()

    accounts = json.loads(Path("var/accounts.json").read_text(encoding="utf-8"))
    account = next(a for a in accounts if a["login"].lower() == args.account.lower())

    with httpx.Client(proxy=args.proxy_url, timeout=45, follow_redirects=False) as client:
        client.get("https://www.roblox.com/login",
                   headers={**H, "Accept": "text/html", "sec-fetch-mode": "navigate",
                            "sec-fetch-dest": "document", "sec-fetch-site": "none"})
        r0 = client.post(LOGIN, headers=H,
                         json={"ctype": "Username", "cvalue": "probe_zz", "password": "x"})
        csrf = r0.headers.get("x-csrf-token") or ""
        headers = dict(H)
        if csrf:
            headers["X-CSRF-TOKEN"] = csrf

        nonce = client.get("https://apis.roblox.com/hba-service/v1/getServerNonce",
                           headers={"User-Agent": UA, "Accept": "application/json",
                                    "Origin": "https://www.roblox.com"}).json()
        sai = make_sai(str(nonce))
        body = {"ctype": "Username", "cvalue": account["login"],
                "password": account["password"], "secureAuthenticationIntent": sai}
        r1 = client.post(LOGIN, headers=headers, json=body)
        meta = b64json(r1.headers.get("rblx-challenge-metadata", ""))
        ch_id = r1.headers.get("rblx-challenge-id", "")
        ch_type = r1.headers.get("rblx-challenge-type", "")
        session_id = meta.get("sessionId") or ch_id
        generic = (meta.get("sharedParameters") or {}).get("genericChallengeId")
        print(f"вход {r1.status_code}; тип {ch_type!r}; sessionId={session_id}; generic={generic}")
        print(f"метаданные: {json.dumps(meta, ensure_ascii=False)}")

        shapes = [
            ("sessionId", {"sessionId": session_id}),
            ("challengeId", {"challengeId": session_id}),
            ("id+type", {"challengeId": session_id, "challengeType": ch_type}),
            ("generic", {"challengeId": generic, "challengeType": ch_type}),
            ("meta+type", {"challengeId": session_id, "challengeType": ch_type,
                           "challengeMetadata": meta}),
            ("path+method", {"challengeId": session_id, "challengeType": ch_type,
                             "requestPath": "/v2/login", "requestMethod": "POST"}),
            ("empty", {}),
            ("solution0", {"challengeId": session_id, "challengeType": "proofofwork",
                           "solution": "0"}),
            ("answer0", {"challengeId": session_id, "challengeType": "proofofwork",
                         "answer": "0"}),
        ]

        results = []
        for label, payload in shapes:
            h = dict(headers)
            h["rblx-challenge-id"] = ch_id or str(session_id)
            h["rblx-challenge-type"] = ch_type or "proofofwork"
            try:
                r = client.post(CONTINUE, headers=h, json=payload)
                text = r.text[:300]
            except Exception as exc:
                r, text = None, f"{type(exc).__name__}"
            status = r.status_code if r is not None else "-"
            print(f"  {label:<14} -> {status} {text!r}")
            results.append({"shape": label, "payload": payload, "status": status,
                            "body": text})

        Path(args.out).write_text(json.dumps(
            {"meta": meta, "type": ch_type, "results": results},
            ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
