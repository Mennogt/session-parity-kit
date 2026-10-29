"""Второй `continue`: отправка токена `captcha` в цепочку проверки.

Порядок как у клиента:

    PoW -> continue -> captchav2 -> /v2/captcha -> redemption_token
        -> continue(тип captchav2, метаданные с токеном) -> повтор входа

    python -u -m probes.redeem_continue --account example_account
"""

from __future__ import annotations

import argparse
import base64
import json
import time
from pathlib import Path

import httpx

import probes.pow_full as powmod

UA = powmod.UA
H = powmod.H
LOGIN = powmod.LOGIN
PUZZLE = powmod.PUZZLE
CONTINUE = "https://apis.roblox.com/challenge/v1/continue"
CAPTCHA = "https://apis.roblox.com/v2/captcha?urlLocale=en_us"


def b64(obj) -> str:
    if isinstance(obj, str):
        return base64.b64encode(obj.encode()).decode()
    return base64.b64encode(json.dumps(obj).encode()).decode()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="example_account")
    args = ap.parse_args()

    accounts = json.loads(Path("var/accounts.json").read_text(encoding="utf-8"))
    account = next(a for a in accounts if a["login"].lower() == args.account.lower())
    print(f"аккаунт: {account['login']}", flush=True)

    with httpx.Client(timeout=120, follow_redirects=False) as client:
        client.get("https://www.roblox.com/login", headers={**H, "Accept": "text/html"})
        r0 = client.post(LOGIN, headers=H,
                         json={"ctype": "Username", "cvalue": "p", "password": "x"})
        headers = dict(H)
        if r0.headers.get("x-csrf-token"):
            headers["X-CSRF-TOKEN"] = r0.headers["x-csrf-token"]

        nonce = client.get("https://apis.roblox.com/hba-service/v1/getServerNonce",
                           headers={"User-Agent": UA}).json()
        body = {"ctype": "Username", "cvalue": account["login"],
                "password": account["password"],
                "secureAuthenticationIntent": powmod.make_sai(str(nonce))}
        r1 = client.post(LOGIN, headers=headers, json=body)
        if r1.headers.get("x-csrf-token"):
            headers["X-CSRF-TOKEN"] = r1.headers["x-csrf-token"]
        meta = powmod.b64json(r1.headers.get("rblx-challenge-metadata", ""))
        sid = meta.get("sessionId")
        generic = (meta.get("sharedParameters") or {}).get("genericChallengeId")

        puzzle = client.get(PUZZLE, headers={"User-Agent": UA},
                            params={"sessionID": sid}).json()
        art = puzzle["artifacts"]
        if isinstance(art, str):
            art = json.loads(art)
        solution = str(pow(int(art["A"]), 1 << int(art["T"]), int(art["N"])))
        verdict = client.post(PUZZLE, headers=headers,
                              json={"sessionID": sid, "solution": solution}).json()
        pow_token = verdict.get("redemptionToken")
        print(f"1) PoW-токен {'есть' if pow_token else 'нет'}", flush=True)
        if not pow_token:
            return 0

        cont = client.post(CONTINUE, headers=headers,
                           json={"challengeId": generic, "challengeType": "proofofwork",
                                 "challengeMetadata": json.dumps(
                                     {**meta, "redemptionToken": pow_token})}).json()
        inner = cont.get("challengeMetadata")
        if isinstance(inner, str):
            inner = json.loads(inner)
        cid = cont.get("challengeId")
        ctype = cont.get("challengeType")
        print(f"2) ступень {ctype} id={cid}", flush=True)

        cap = client.post(CAPTCHA, headers=headers, json={"challenge_id": cid})
        redeem = ""
        try:
            redeem = cap.json().get("redemption_token", "")
        except Exception:
            pass
        print(f"3) captcha {cap.status_code}, токен {'есть' if redeem else 'нет'}", flush=True)
        if not redeem:
            return 0

        variants = [
            ("dict", json.dumps({"redemptionToken": redeem})),
            ("snake", json.dumps({"redemption_token": redeem})),
            ("строка", redeem),
        ]
        for label, md in variants:
            r = client.post(CONTINUE, headers=headers,
                            json={"challengeId": cid, "challengeType": ctype or "captchav2",
                                  "challengeMetadata": md})
            print(f"4) continue[{label}] -> {r.status_code} {r.text[:160]!r}", flush=True)
            if r.status_code != 200:
                continue
            for name, hdr in (
                ("без заголовков", dict(headers)),
                ("с токеном", {**headers, "rblx-challenge-id": str(cid),
                               "rblx-challenge-type": ctype or "captchav2",
                               "rblx-challenge-metadata": b64({"redemptionToken": redeem})}),
            ):
                rr = client.post(LOGIN, headers=hdr, json=body)
                jars = {c.name: c.value for c in client.cookies.jar}
                print(f"5) вход[{name}] -> {rr.status_code} {rr.text[:110]!r} "
                      f"cookie={sorted(jars)}", flush=True)
                if ".ROBLOSECURITY" in jars:
                    print("   СЕССИЯ ПОЛУЧЕНА", flush=True)
                    (Path("var") / f"session-{account['login']}.json").write_text(
                        json.dumps(jars, ensure_ascii=False, indent=2), encoding="utf-8")
                    return 0
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
