"""Завершение входа токеном, который выдаёт `/v2/captcha`.

`POST apis.roblox.com/v2/captcha {"challenge_id": "<id второй ступени>"}`
может ответить `200 {"redemption_token": "..."}` — без интерактива.
Проверяем, принимает ли платформа этот токен при повторе входа.

    python -u -m probes.redeem_captchav2 --account example_account
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
    ap.add_argument("--out", default="var/redeem-captchav2.json")
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
        print(f"1) тип {r1.headers.get('rblx-challenge-type')}", flush=True)

        puzzle = client.get(PUZZLE, headers={"User-Agent": UA},
                            params={"sessionID": sid}).json()
        art = puzzle["artifacts"]
        if isinstance(art, str):
            art = json.loads(art)
        started = time.perf_counter()
        solution = str(pow(int(art["A"]), 1 << int(art["T"]), int(art["N"])))
        print(f"2) PoW за {time.perf_counter() - started:.1f} с", flush=True)
        verdict = client.post(PUZZLE, headers=headers,
                              json={"sessionID": sid, "solution": solution}).json()
        token = verdict.get("redemptionToken")
        print(f"3) PoW-токен {'есть' if token else 'нет'}", flush=True)
        if not token:
            return 0

        md = {**meta, "redemptionToken": token}
        cont = client.post(CONTINUE, headers=headers,
                           json={"challengeId": generic, "challengeType": "proofofwork",
                                 "challengeMetadata": json.dumps(md)}).json()
        inner = cont.get("challengeMetadata")
        if isinstance(inner, str):
            inner = json.loads(inner)
        next_id = cont.get("challengeId")
        next_type = cont.get("challengeType")
        print(f"4) вторая ступень {next_type} id={next_id}", flush=True)

        cap = client.post(CAPTCHA, headers=headers, json={"challenge_id": next_id})
        print(f"5) captcha: {cap.status_code} {cap.text[:160]}", flush=True)
        redeem = ""
        try:
            redeem = cap.json().get("redemption_token", "")
        except Exception:
            pass
        if not redeem:
            print("   токена нет — дальше проверять нечего")
            return 0

        attempts = [
            ("только токен", {**headers, "rblx-challenge-id": str(next_id),
                              "rblx-challenge-type": next_type or "captchav2",
                              "rblx-challenge-metadata": b64({"redemptionToken": redeem})}),
            ("токен+id", {**headers, "rblx-challenge-id": str(next_id),
                          "rblx-challenge-type": next_type or "captchav2",
                          "rblx-challenge-metadata": b64(
                              {"challengeId": next_id, "redemptionToken": redeem})}),
            ("snake_case", {**headers, "rblx-challenge-id": str(next_id),
                            "rblx-challenge-type": next_type or "captchav2",
                            "rblx-challenge-metadata": b64(
                                {"redemption_token": redeem})}),
            ("полный блок", {**headers, "rblx-challenge-id": str(next_id),
                             "rblx-challenge-type": next_type or "captchav2",
                             "rblx-challenge-metadata": b64(
                                 {**(inner or {}), "redemptionToken": redeem})}),
            ("с x-retry", {**headers, "rblx-challenge-id": str(next_id),
                           "rblx-challenge-type": next_type or "captchav2",
                           "rblx-challenge-metadata": b64({"redemptionToken": redeem}),
                           "x-retry-attempt": "1"}),
        ]

        results = []
        for label, hdr in attempts:
            r = client.post(LOGIN, headers=hdr, json=body)
            jars = {c.name: c.value for c in client.cookies.jar}
            print(f"6) {label:<14} -> {r.status_code} {r.text[:110]!r} "
                  f"cookie={sorted(jars)}", flush=True)
            results.append({"case": label, "status": r.status_code,
                            "body": r.text[:200], "cookies": sorted(jars)})
            if ".ROBLOSECURITY" in jars:
                print("   СЕССИЯ ПОЛУЧЕНА", flush=True)
                (Path("var") / f"session-{account['login']}.json").write_text(
                    json.dumps(jars, ensure_ascii=False, indent=2), encoding="utf-8")
                break
            time.sleep(1)

        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
