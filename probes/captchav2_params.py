"""Параметры второй ступени: где взять sitekey и blob для Arkose.

После PoW платформа отдаёт `captchav2` с общими метаданными. Клиент на этом
этапе обращается за параметрами отдельно. Проверяем известные точки:
`apis.roblox.com/v2/captcha` и `captcha/v1/metadata`.

    python -u -m probes.captchav2_params --account <login>
"""

from __future__ import annotations

import argparse
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="<login>")
    ap.add_argument("--out", default="var/captchav2-params.json")
    args = ap.parse_args()

    accounts = json.loads(Path("var/accounts.json").read_text(encoding="utf-8"))
    account = next(a for a in accounts if a["login"].lower() == args.account.lower())
    print(f"аккаунт {account['login']}", flush=True)

    report: dict = {}
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
                "password": account["password"],
                "secureAuthenticationIntent": powmod.make_sai(str(nonce))}
        r1 = client.post(LOGIN, headers=headers, json=body)
        if r1.headers.get("x-csrf-token"):
            headers["X-CSRF-TOKEN"] = r1.headers["x-csrf-token"]
        meta = powmod.b64json(r1.headers.get("rblx-challenge-metadata", ""))
        sid = meta.get("sessionId")
        ch_type = r1.headers.get("rblx-challenge-type", "")
        generic = (meta.get("sharedParameters") or {}).get("genericChallengeId")
        print(f"1) тип {ch_type}, session {sid}", flush=True)

        p = client.get(PUZZLE, headers={"User-Agent": UA},
                       params={"sessionID": sid}).json()
        art = p.get("artifacts")
        if isinstance(art, str):
            art = json.loads(art)
        print(f"   T={art.get('T')}", flush=True)
        t0 = time.perf_counter()
        solution = str(pow(int(art["A"]), 1 << int(art["T"]), int(art["N"])))
        print(f"2) решение за {time.perf_counter() - t0:.1f} с", flush=True)
        v = client.post(PUZZLE, headers=headers,
                        json={"sessionID": sid, "solution": solution}).json()
        token = v.get("redemptionToken")
        print(f"3) токен {'есть' if token else 'нет'}", flush=True)
        if not token:
            print("   ответ:", json.dumps(v, ensure_ascii=False)[:200])
            return 0

        md = {**meta, "redemptionToken": token}
        rc = client.post(CONTINUE, headers=headers,
                         json={"challengeId": generic, "challengeType": ch_type,
                               "challengeMetadata": json.dumps(md)})
        nxt = rc.json()
        print(f"4) continue {rc.status_code}, следующая {nxt.get('challengeType')}", flush=True)
        next_id = nxt.get("challengeId")
        inner = nxt.get("challengeMetadata")
        if isinstance(inner, str):
            inner = json.loads(inner)
        gid = ((inner or {}).get("sharedParameters") or {}).get("genericChallengeId")

        probes = [
            ("POST /v2/captcha", "POST", "https://apis.roblox.com/v2/captcha?urlLocale=en_us",
             {"challenge_id": gid or next_id}),
            ("POST /v2/captcha sessionId", "POST",
             "https://apis.roblox.com/v2/captcha?urlLocale=en_us",
             {"challenge_id": next_id}),
            ("GET captcha/v1/metadata", "GET",
             "https://apis.roblox.com/captcha/v1/metadata", None),
            ("GET /v2/captcha", "GET",
             f"https://apis.roblox.com/v2/captcha?challenge_id={gid or next_id}", None),
        ]
        for label, method, url, payload in probes:
            try:
                if method == "POST":
                    r = client.post(url, headers=headers, json=payload)
                else:
                    r = client.get(url, headers={"User-Agent": UA, "Accept": "application/json"})
                print(f"5) {label:<28} -> {r.status_code} {r.text[:300]!r}", flush=True)
                report[label] = {"status": r.status_code, "body": r.text[:1500]}
            except Exception as exc:
                print(f"5) {label:<28} ошибка {type(exc).__name__}", flush=True)

    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
