"""Сколько трафика и времени стоит один вход по HTTP-пути.

Считает тела ответов на каждом шаге цепочки и печатает смету.

    python -u -m tools.traffic_report --account example_account
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
NONCE = "https://apis.roblox.com/hba-service/v1/getServerNonce"
VERIFY = "https://users.roblox.com/v1/users/authenticated"


class Meter:
    def __init__(self) -> None:
        self.rows: list[tuple[str, int, int]] = []

    def hook(self, response: httpx.Response) -> None:
        try:
            body = len(response.content)
        except Exception:
            body = 0
        head = sum(len(k) + len(str(v)) + 4 for k, v in response.headers.items())
        self.rows.append((str(response.request.url).split("?")[0][:58],
                          response.status_code, body + head))

    def total(self) -> int:
        return sum(row[2] for row in self.rows)

    def show(self) -> None:
        print(f"{'шаг':<60}{'код':>5}{'байт':>10}")
        for url, code, size in self.rows:
            print(f"{url:<60}{code:>5}{size:>10}")
        print(f"{'ИТОГО':<60}{'':>5}{self.total():>10}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True)
    ap.add_argument("--proxy-url", default=None)
    args = ap.parse_args()

    accounts = json.loads(Path("var/accounts.json").read_text(encoding="utf-8"))
    account = next(a for a in accounts if a["login"].lower() == args.account.lower())

    meter = Meter()
    started = time.perf_counter()
    with httpx.Client(proxy=args.proxy_url, timeout=90, follow_redirects=False,
                      event_hooks={"response": [meter.hook]}) as client:
        client.get("https://www.roblox.com/login", headers={**H, "Accept": "text/html"})
        client.get("https://www.roblox.com/home", headers={**H, "Accept": "text/html"})

        r0 = client.post(LOGIN, headers=H,
                         json={"ctype": "Username", "cvalue": "probe", "password": "x"})
        headers = dict(H)
        if r0.headers.get("x-csrf-token"):
            headers["X-CSRF-TOKEN"] = r0.headers["x-csrf-token"]

        nonce = client.get(NONCE, headers={"User-Agent": UA,
                                           "Accept": "application/json"}).json()
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
        solve_started = time.perf_counter()
        solution = str(pow(int(art["A"]), 1 << int(art["T"]), int(art["N"])))
        solve_seconds = time.perf_counter() - solve_started
        verdict = client.post(PUZZLE, headers=headers,
                              json={"sessionID": sid, "solution": solution}).json()
        token = verdict.get("redemptionToken")
        if not token:
            print("нет PoW-токена:", json.dumps(verdict)[:160])
            meter.show()
            return 0

        cont = client.post(CONTINUE, headers=headers,
                           json={"challengeId": generic, "challengeType": "proofofwork",
                                 "challengeMetadata": json.dumps(
                                     {**meta, "redemptionToken": token})}).json()
        stage_id = cont.get("challengeId")
        stage_type = cont.get("challengeType") or "captchav2"
        cap = client.post(CAPTCHA, headers=headers, json={"challenge_id": stage_id}).json()
        redeem = cap.get("redemption_token", "")
        if not redeem:
            print("вторая ступень потребовала интерактив")
            meter.show()
            return 0
        client.post(CONTINUE, headers=headers,
                    json={"challengeId": stage_id, "challengeType": stage_type,
                          "challengeMetadata": json.dumps({"redemptionToken": redeem})})
        retry_headers = dict(headers)
        retry_headers.update({
            "rblx-challenge-id": str(stage_id),
            "rblx-challenge-type": stage_type,
            "rblx-challenge-metadata": base64.b64encode(
                json.dumps({"redemptionToken": redeem}).encode()).decode(),
        })
        final = client.post(LOGIN, headers=retry_headers, json=body)
        jars = {c.name: c.value for c in client.cookies.jar}
        if ".ROBLOSECURITY" in jars:
            client.get(VERIFY, headers={"User-Agent": UA, "Accept": "application/json"})

    elapsed = time.perf_counter() - started
    meter.show()
    print(f"\nвремя: {elapsed:.1f} с (решение PoW {solve_seconds:.2f} с)")
    print(f"трафик: {meter.total() / 1024:.0f} КБ на один вход")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
