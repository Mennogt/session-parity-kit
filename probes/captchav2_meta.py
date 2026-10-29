"""Полные метаданные второй ступени (captchav2).

Нужно увидеть, что именно платформа просит на втором этапе: sitekey,
blob обмена, appType, режим. Это входные данные для Arkose.

    python -u -m probes.captchav2_meta --account <login>
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="<login>")
    ap.add_argument("--out", default="var/captchav2-meta.json")
    args = ap.parse_args()

    accounts = json.loads(Path("var/accounts.json").read_text(encoding="utf-8"))
    account = next(a for a in accounts if a["login"].lower() == args.account.lower())

    with httpx.Client(timeout=180, follow_redirects=False) as client:
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
        ch_id = r1.headers.get("rblx-challenge-id", "")
        ch_type = r1.headers.get("rblx-challenge-type", "")
        sid = meta.get("sessionId")
        generic = (meta.get("sharedParameters") or {}).get("genericChallengeId")
        print(f"1) тип {ch_type}; session {sid}", flush=True)

        p = client.get(PUZZLE, headers={"User-Agent": UA},
                       params={"sessionID": sid}).json()
        art = p.get("artifacts")
        if isinstance(art, str):
            art = json.loads(art)
        print(f"   T={art.get('T')}", flush=True)
        t0 = time.perf_counter()
        solution = str(pow(int(art["A"]), 1 << int(art["T"]), int(art["N"])))
        print(f"2) решение за {time.perf_counter() - t0:.0f} с", flush=True)
        v = client.post(PUZZLE, headers=headers,
                        json={"sessionID": sid, "solution": solution}).json()
        token = v.get("redemptionToken")
        print(f"3) проверка: {v.get('answerCorrect')}, токен {'есть' if token else 'нет'}",
              flush=True)
        if not token:
            return 0

        md = {**meta, "redemptionToken": token}
        rc = client.post("https://apis.roblox.com/challenge/v1/continue",
                         headers=headers,
                         json={"challengeId": generic or ch_id, "challengeType": ch_type,
                               "challengeMetadata": json.dumps(md)})
        print(f"4) continue: {rc.status_code}", flush=True)
        text = rc.text
        Path(args.out).write_text(text, encoding="utf-8")
        try:
            data = rc.json()
        except Exception:
            print("   тело не JSON:", text[:300])
            return 0
        print(f"   следующая: {data.get('challengeType')}", flush=True)
        inner = data.get("challengeMetadata")
        if isinstance(inner, str):
            try:
                inner = json.loads(inner)
            except Exception:
                pass
        print("=== МЕТАДАННЫЕ ВТОРОЙ СТУПЕНИ ===")
        print(json.dumps(inner, ensure_ascii=False, indent=2)[:2500])
        Path("var/captchav2-inner.json").write_text(
            json.dumps(inner, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
