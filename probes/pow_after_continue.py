"""Что делать после успешного `continue`.

После PoW и `challenge/v1/continue` (200) платформа отдаёт следующую
проверку `captchav2`. Проверяем четыре способа завершить вход, не трогая
этот второй этап:

  A. просто повторить вход тем же телом (без заголовков проверки);
  B. то же с `x-retry-attempt`;
  C. с исходным идентификатором и метаданными проверки, где лежит токен;
  D. повторить вход по адресу с тем же query, что у клиента
     (`?urlLocale=en_us`).

    python -u -m probes.pow_after_continue --account <login>
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


def b64(d) -> str:
    if isinstance(d, str):
        return base64.b64encode(d.encode()).decode()
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
    ap.add_argument("--out", default="var/pow-after-continue.json")
    args = ap.parse_args()

    accounts = json.loads(Path("var/accounts.json").read_text(encoding="utf-8"))
    account = next(a for a in accounts if a["login"].lower() == args.account.lower())
    print(f"аккаунт: {account['login']}", flush=True)

    with httpx.Client(timeout=120, follow_redirects=False) as client:
        client.get("https://www.roblox.com/login", headers={**H, "Accept": "text/html"})
        r0 = client.post(LOGIN, headers=H,
                         json={"ctype": "Username", "cvalue": "probe_zz", "password": "x"})
        headers = dict(H)
        if r0.headers.get("x-csrf-token"):
            headers["X-CSRF-TOKEN"] = r0.headers["x-csrf-token"]

        nonce = client.get("https://apis.roblox.com/hba-service/v1/getServerNonce",
                           headers={"User-Agent": UA}).json()
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
        print(f"1) вход {r1.status_code}, тип {ch_type}", flush=True)
        if not sid:
            print(f"   {r1.text[:160]}")
            return 0

        p = client.get(PUZZLE, headers={"User-Agent": UA},
                       params={"sessionID": sid}).json()
        art = p.get("artifacts")
        if isinstance(art, str):
            art = json.loads(art)
        t0 = time.perf_counter()
        solution = str(pow(int(art["A"]), 1 << int(art["T"]), int(art["N"])))
        print(f"2) решение за {time.perf_counter() - t0:.0f} с", flush=True)
        v = client.post(PUZZLE, headers=headers,
                        json={"sessionID": sid, "solution": solution}).json()
        token = v.get("redemptionToken")
        print(f"3) проверка: correct={v.get('answerCorrect')} token={'есть' if token else 'нет'}",
              flush=True)
        if not token:
            return 0

        md = {**meta, "redemptionToken": token}
        rc = client.post(CONTINUE, headers=headers,
                         json={"challengeId": generic or ch_id, "challengeType": ch_type,
                               "challengeMetadata": json.dumps(md)})
        print(f"4) continue: {rc.status_code}", flush=True)
        nxt = {}
        try:
            nxt = rc.json()
        except Exception:
            pass
        print(f"   следующая: {nxt.get('challengeType')}", flush=True)

        attempts = [
            ("A без заголовков", LOGIN, dict(headers)),
            ("B x-retry-attempt", LOGIN, {**headers, "x-retry-attempt": "1"}),
            ("C исходный id + токен", LOGIN,
             {**headers, "rblx-challenge-id": ch_id or str(generic), "rblx-challenge-type": ch_type,
              "rblx-challenge-metadata": b64({"redemptionToken": token})}),
            ("D с query", f"{LOGIN}?urlLocale=en_us", dict(headers)),
            ("E полный блок", LOGIN,
             {**headers, "rblx-challenge-id": ch_id or str(generic), "rblx-challenge-type": ch_type,
              "rblx-challenge-metadata": b64(md)}),
        ]
        results = []
        for label, url, hdrs in attempts:
            r = client.post(url, headers=hdrs, json=body)
            jars = {c.name: c.value for c in client.cookies.jar}
            print(f"   {label:<24} -> {r.status_code} {r.text[:90]!r} "
                  f"cookie={sorted(jars)}", flush=True)
            results.append({"case": label, "status": r.status_code, "body": r.text[:200],
                            "cookies": sorted(jars)})
            if ".ROBLOSECURITY" in jars:
                print("   СЕССИЯ ПОЛУЧЕНА", flush=True)
                (Path("var") / f"session-{account['login']}.json").write_text(
                    json.dumps(jars, ensure_ascii=False, indent=2), encoding="utf-8")
                break
            time.sleep(1.0)

    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
