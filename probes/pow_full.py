"""Полный проход proof-of-work чистым HTTP.

Протокол восстановлен из клиентских бандлов и подтверждён живым запросом:

  1. подпись `secureAuthenticationIntent` -> тип проверки `proofofwork`;
  2. `GET  apis.roblox.com/proof-of-work-service/v1/pow-puzzle?sessionID=...`
     отдаёт `{"puzzleType":"1","artifacts":"{\"N\":...,\"A\":...,\"T\":400000}"}`;
  3. решение — последовательное возведение в квадрат: `A^(2^T) mod N`;
  4. `POST .../v1/pow-puzzle` с `{sessionID, solution}` -> `redemptionToken`;
  5. повторный вход с заголовками `rblx-challenge-*`.

    python -m probes.pow_full --account <login>
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
    return {
        "clientPublicKey": pub,
        "clientEpochTimestamp": ts,
        "serverNonce": nonce,
        "saiSignature": base64.b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode(),
    }


def solve_time_lock(artifacts: dict) -> tuple[str, float]:
    n = int(artifacts["N"])
    a = int(artifacts["A"])
    t = int(artifacts["T"])
    started = time.perf_counter()
    # A^(2^T) mod N: показатель 2^T, бинарное возведение в C-скорости.
    result = pow(a, 1 << t, n)
    return str(result), time.perf_counter() - started


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="<login>")
    ap.add_argument("--proxy-url", default=None)
    ap.add_argument("--out", default="var/pow-full.json")
    args = ap.parse_args()

    accounts = json.loads(Path("var/accounts.json").read_text(encoding="utf-8"))
    account = next(a for a in accounts if a["login"].lower() == args.account.lower())
    report: dict = {"account": account["login"]}

    with httpx.Client(proxy=args.proxy_url, timeout=90, follow_redirects=False) as client:
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
        body = {"ctype": "Username", "cvalue": account["login"],
                "password": account["password"], "secureAuthenticationIntent": make_sai(str(nonce))}
        r1 = client.post(LOGIN, headers=headers, json=body)
        meta_raw = r1.headers.get("rblx-challenge-metadata", "")
        ch_id = r1.headers.get("rblx-challenge-id", "")
        ch_type = r1.headers.get("rblx-challenge-type", "")
        meta = b64json(meta_raw)
        session_id = meta.get("sessionId")
        print(f"1) вход {r1.status_code}; тип {ch_type}; sessionId {session_id}")
        report["login"] = {"status": r1.status_code, "type": ch_type, "session": session_id}
        if not session_id:
            print(f"   тело: {r1.text[:200]}")
            return 0

        p = client.get(PUZZLE, headers={"User-Agent": UA, "Accept": "application/json"},
                       params={"sessionID": session_id})
        print(f"2) задание: {p.status_code} {p.text[:220]}")
        report["puzzle"] = {"status": p.status_code, "body": p.text[:600]}
        if p.status_code != 200:
            return 0
        payload = p.json()
        artifacts = payload.get("artifacts")
        if isinstance(artifacts, str):
            artifacts = json.loads(artifacts)
        print(f"   N: {len(str(artifacts.get('N')))} цифр, A={artifacts.get('A')}, "
              f"T={artifacts.get('T')}")

        solution, seconds = solve_time_lock(artifacts)
        print(f"3) решение найдено за {seconds:.1f} с, длина {len(solution)}")
        report["solve_seconds"] = round(seconds, 2)

        v = client.post(PUZZLE, headers=headers,
                        json={"sessionID": session_id, "solution": solution})
        print(f"4) проверка решения: {v.status_code} {v.text[:300]}")
        report["verify"] = {"status": v.status_code, "body": v.text[:600]}
        try:
            answer = v.json()
        except Exception:
            answer = {}
        token = answer.get("redemptionToken") or answer.get("redemption_token")
        correct = answer.get("answerCorrect")
        print(f"   верно: {correct}; токен: {'есть' if token else 'нет'}")

        if token:
            patched = dict(meta)
            patched["redemptionToken"] = token
            retry_headers = dict(headers)
            retry_headers.update({
                "rblx-challenge-id": ch_id or str(session_id),
                "rblx-challenge-type": ch_type or "proofofwork",
                "rblx-challenge-metadata": base64.b64encode(
                    json.dumps(patched).encode()).decode(),
                "x-retry-attempt": "1",
            })
            r3 = client.post(LOGIN, headers=retry_headers, json=body)
            jars = {c.name: c.value for c in client.cookies.jar}
            print(f"5) повторный вход: {r3.status_code} {r3.text[:200]}")
            print(f"   cookie: {sorted(jars)}")
            report["retry"] = {"status": r3.status_code, "body": r3.text[:300],
                               "cookies": sorted(jars)}
            if ".ROBLOSECURITY" in jars:
                print("   СЕССИЯ ПОЛУЧЕНА ЧЕРЕЗ HTTP")
                (Path("var") / f"session-{account['login']}.json").write_text(
                    json.dumps(jars, ensure_ascii=False, indent=2), encoding="utf-8")

    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
