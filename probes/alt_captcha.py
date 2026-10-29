"""Альтернативная капча: что отдаёт `/v2/alt-captcha` на второй ступени.

В клиенте выбор такой:

    tq.post(mode === "CAPTCHA_MODE_MONITOR" ? altCaptcha : captcha,
            {challenge_id})

То есть `alt-captcha` — путь для режима наблюдения, когда платформа не
блокирует. Проверяем, что он вернёт на нашей проверке `captchav2`
(режим `CAPTCHA_MODE_INVALID`), и можно ли из ответа достать токен.

    python -u -m probes.alt_captcha
"""

from __future__ import annotations

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


def main() -> int:
    accounts = json.loads(Path("var/accounts.json").read_text(encoding="utf-8"))
    account = next(a for a in accounts if a["login"] == "example_account")

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
        print(f"1) тип {r1.headers.get('rblx-challenge-type')}, session {sid}", flush=True)

        puzzle = client.get(PUZZLE, headers={"User-Agent": UA},
                            params={"sessionID": sid}).json()
        art = puzzle["artifacts"]
        if isinstance(art, str):
            art = json.loads(art)
        started = time.perf_counter()
        solution = str(pow(int(art["A"]), 1 << int(art["T"]), int(art["N"])))
        print(f"2) решение за {time.perf_counter() - started:.1f} с", flush=True)
        verdict = client.post(PUZZLE, headers=headers,
                              json={"sessionID": sid, "solution": solution}).json()
        token = verdict.get("redemptionToken")
        print(f"3) токен {'есть' if token else 'нет'}", flush=True)
        if not token:
            return 0

        md = {**meta, "redemptionToken": token}
        cont = client.post(CONTINUE, headers=headers,
                           json={"challengeId": generic, "challengeType": "proofofwork",
                                 "challengeMetadata": json.dumps(md)})
        nxt = cont.json()
        inner = nxt.get("challengeMetadata")
        if isinstance(inner, str):
            inner = json.loads(inner)
        next_id = nxt.get("challengeId")
        print(f"4) вторая ступень: {nxt.get('challengeType')} id={next_id} "
              f"mode={(inner or {}).get('mode')}", flush=True)

        for label, cid in (("challengeId", next_id), ("generic", generic),
                           ("sessionId", sid)):
            for path in ("/v2/alt-captcha", "/v2/captcha"):
                r = client.post("https://apis.roblox.com" + path, headers=headers,
                                json={"challenge_id": cid})
                print(f"5) {label:<12} {path:<16} -> {r.status_code} {r.text[:260]!r}",
                      flush=True)
                if r.status_code == 200:
                    (Path("var") / "alt-captcha-response.json").write_text(
                        r.text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
