"""Распределение исходов входа по аккаунтам.

Один запрос на аккаунт, без решения задач: нужно понять, кому платформа
вообще не выставляет проверку, кому выдаёт proof-of-work, а кому сразу
капчу. Это отвечает на вопрос, есть ли аккаунты, проходящие вход чистым
HTTP без дополнительных шагов.

    python -u -m probes.outcome_scan --limit 20
"""

from __future__ import annotations

import argparse
import base64
import json
import time
from collections import Counter
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
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://www.roblox.com",
    "Referer": "https://www.roblox.com/login",
}
LOGIN = "https://auth.roblox.com/v2/login"
KEY_FILE = Path("var/sai-key.pem")


def b64json(v: str) -> dict:
    pad = "=" * (-len(v) % 4)
    for dec in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            return json.loads(dec(v + pad))
        except Exception:
            continue
    return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--proxy-url", default=None)
    ap.add_argument("--out", default="var/outcome-scan.json")
    args = ap.parse_args()

    accounts = json.loads(Path("var/accounts.json").read_text(encoding="utf-8"))
    key = serialization.load_pem_private_key(KEY_FILE.read_bytes(), password=None)
    rows: list[dict] = []

    with httpx.Client(proxy=args.proxy_url, timeout=45, follow_redirects=False) as warm:
        warm.get("https://www.roblox.com/login", headers={**H, "Accept": "text/html"})
        guest_cookies = {c.name: c.value for c in warm.cookies.jar}

    for n, account in enumerate(accounts[args.offset: args.offset + args.limit], 1):
        # Свой клиент на каждый аккаунт: cookie одного не должны влиять
        # на проверку следующего.
        client = httpx.Client(proxy=args.proxy_url, timeout=45,
                              follow_redirects=False, cookies=guest_cookies)
        try:
            r0 = client.post(LOGIN, headers=H,
                             json={"ctype": "Username", "cvalue": "probe_zz", "password": "x"})
            headers = dict(H)
            if r0.headers.get("x-csrf-token"):
                headers["X-CSRF-TOKEN"] = r0.headers["x-csrf-token"]
            nonce = client.get("https://apis.roblox.com/hba-service/v1/getServerNonce",
                               headers={"User-Agent": UA}).json()
            spki = key.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
            pub = base64.b64encode(spki).decode()
            ts = int(time.time())
            der = key.sign(f"{pub}|{ts}|{nonce}".encode(), ec.ECDSA(hashes.SHA256()))
            r, s = utils.decode_dss_signature(der)
            sai = {"clientPublicKey": pub, "clientEpochTimestamp": ts, "serverNonce": nonce,
                   "saiSignature": base64.b64encode(
                       r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode()}
            body = {"ctype": "Username", "cvalue": account["login"],
                    "password": account["password"], "secureAuthenticationIntent": sai}
            resp = client.post(LOGIN, headers=headers, json=body)
            jars = {c.name: c.value for c in client.cookies.jar}
            ctype = resp.headers.get("rblx-challenge-type", "")
            if ".ROBLOSECURITY" in jars:
                ident = client.get("https://users.roblox.com/v1/users/authenticated",
                                   headers={"User-Agent": UA, "Accept": "application/json"})
                who = ident.json().get("name") if ident.status_code == 200 else "?"
                outcome = f"СЕССИЯ ({who})"
                (Path("var") / f"session-{account['login']}.json").write_text(
                    json.dumps(jars, ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                outcome = ctype or f"код {resp.status_code}: {resp.text[:40]}"
            rows.append({"login": account["login"], "outcome": outcome,
                         "status": resp.status_code, "type": ctype})
            print(f"  {n:>2}/{args.limit} {account['login']:<26} -> {resp.status_code} "
                  f"{outcome}", flush=True)
        except Exception as exc:
            rows.append({"login": account["login"], "outcome": f"ошибка {type(exc).__name__}"})
            print(f"  {n:>2}/{args.limit} {account['login']:<26} -> ошибка "
                  f"{type(exc).__name__}", flush=True)
        finally:
            client.close()
    counts = Counter(r["outcome"] for r in rows)
    print("\nитоги:", dict(counts), flush=True)
    Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
