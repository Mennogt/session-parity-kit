"""Вторая половина протокола: `challenge/v1/continue` после PoW.

Клиент после проверки решения вызывает
`genericChallenge.continueChallenge(id, type, JSON.stringify(metadata))`,
и только потом повторяет исходный запрос. Проверяем эту последовательность:
сначала continue с полученным токеном, затем повтор входа.

    python -u -m probes.pow_continue_flow --account <login>
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
}
LOGIN = "https://auth.roblox.com/v2/login"
PUZZLE = "https://apis.roblox.com/proof-of-work-service/v1/pow-puzzle"
CONTINUE = "https://apis.roblox.com/challenge/v1/continue"


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="<login>")
    ap.add_argument("--out", default="var/pow-continue.json")
    args = ap.parse_args()

    accounts = json.loads(Path("var/accounts.json").read_text(encoding="utf-8"))
    account = next(a for a in accounts if a["login"].lower() == args.account.lower())
    print(f"аккаунт: {account['login']}", flush=True)

    with httpx.Client(timeout=60, follow_redirects=False) as client:
        client.get("https://www.roblox.com/login", headers={**H, "Accept": "text/html"})
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
        if r1.headers.get("x-csrf-token"):
            headers["X-CSRF-TOKEN"] = r1.headers["x-csrf-token"]
        meta = b64json(r1.headers.get("rblx-challenge-metadata", ""))
        ch_id = r1.headers.get("rblx-challenge-id", "")
        ch_type = r1.headers.get("rblx-challenge-type", "")
        sid = meta.get("sessionId")
        generic = (meta.get("sharedParameters") or {}).get("genericChallengeId")
        print(f"вход {r1.status_code}; тип {ch_type}; session {sid}; generic {generic}", flush=True)

        puzzle = client.get(PUZZLE, headers={"User-Agent": UA, "Accept": "application/json"},
                            params={"sessionID": sid}).json()
        art = puzzle.get("artifacts")
        if isinstance(art, str):
            art = json.loads(art)
        t0 = time.perf_counter()
        solution = str(pow(int(art["A"]), 1 << int(art["T"]), int(art["N"])))
        print(f"решение за {time.perf_counter() - t0:.1f} с", flush=True)
        verdict = client.post(PUZZLE, headers=headers,
                              json={"sessionID": sid, "solution": solution}).json()
        token = verdict.get("redemptionToken")
        print(f"проверка: correct={verdict.get('answerCorrect')} token={'есть' if token else 'нет'}",
              flush=True)
        if not token:
            return 0

        md_variants = {
            "dict_token": {"redemptionToken": token},
            "dict_full": {**meta, "redemptionToken": token},
            "dict_sid": {"sessionId": sid, "redemptionToken": token},
        }
        results = []
        for name, md in md_variants.items():
            for id_value in (generic or ch_id, ch_id, sid):
                payload = {"challengeId": id_value, "challengeType": ch_type or "proofofwork",
                           "challengeMetadata": json.dumps(md)}
                r = client.post(CONTINUE, headers=headers, json=payload)
                label = f"{name}/{str(id_value)[:18]}"
                print(f"  continue {label:<34} -> {r.status_code} {r.text[:110]!r}", flush=True)
                results.append({"variant": label, "status": r.status_code, "body": r.text[:300]})
                if r.status_code == 200:
                    # Повтор входа с заголовками проверки.
                    h2 = dict(headers)
                    h2.update({"rblx-challenge-id": str(id_value),
                               "rblx-challenge-type": ch_type or "proofofwork",
                               "rblx-challenge-metadata": b64(md)})
                    r2 = client.post(LOGIN, headers=h2, json=body)
                    jars = {c.name: c.value for c in client.cookies.jar}
                    print(f"     повтор входа: {r2.status_code} {r2.text[:120]!r} "
                          f"cookie={sorted(jars)}", flush=True)
                    results.append({"variant": label, "retry": r2.status_code,
                                    "retry_body": r2.text[:200], "cookies": sorted(jars)})
                    if ".ROBLOSECURITY" in jars:
                        print("     СЕССИЯ ПОЛУЧЕНА", flush=True)
                        (Path("var") / f"session-{account['login']}.json").write_text(
                            json.dumps(jars, ensure_ascii=False, indent=2), encoding="utf-8")
                        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                                  encoding="utf-8")
                        return 0
                time.sleep(1.0)

    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
