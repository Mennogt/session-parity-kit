"""Постоянный ключ подписи: одно «устройство» на серию попыток.

Браузер хранит ключ ECDSA и подписывает им каждый вход, поэтому платформа
видит одно и то же устройство. Мы генерировали новый ключ на каждую попытку
и выглядели как новое устройство каждый раз — вероятная причина эскалации
проверок.

Скрипт создаёт ключ один раз (var/sai-key.pem), затем делает серию входов
этим ключом и печатает тип проверки на каждом шаге.

    python -u -m probes.sai_persist --account <login> --attempts 6
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
KEY_FILE = Path("var/sai-key.pem")


def load_key():
    if KEY_FILE.exists():
        return serialization.load_pem_private_key(KEY_FILE.read_bytes(), password=None)
    key = ec.generate_private_key(ec.SECP256R1())
    KEY_FILE.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    print(f"ключ создан: {KEY_FILE}")
    return key


def sai(key, nonce: str) -> dict:
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
    ap.add_argument("--attempts", type=int, default=6)
    ap.add_argument("--pause", type=float, default=8.0)
    ap.add_argument("--out", default="var/sai-persist.json")
    args = ap.parse_args()

    accounts = json.loads(Path("var/accounts.json").read_text(encoding="utf-8"))
    account = next(a for a in accounts if a["login"].lower() == args.account.lower())
    key = load_key()
    print(f"аккаунт {account['login']}, ключ постоянный", flush=True)

    rows = []
    with httpx.Client(timeout=60, follow_redirects=False) as client:
        client.get("https://www.roblox.com/login", headers={**H, "Accept": "text/html"})
        for i in range(1, args.attempts + 1):
            r0 = client.post(LOGIN, headers=H,
                             json={"ctype": "Username", "cvalue": "probe_zz", "password": "x"})
            headers = dict(H)
            if r0.headers.get("x-csrf-token"):
                headers["X-CSRF-TOKEN"] = r0.headers["x-csrf-token"]
            nonce = client.get("https://apis.roblox.com/hba-service/v1/getServerNonce",
                               headers={"User-Agent": UA}).json()
            body = {"ctype": "Username", "cvalue": account["login"],
                    "password": account["password"],
                    "secureAuthenticationIntent": sai(key, str(nonce))}
            r = client.post(LOGIN, headers=headers, json=body)
            jars = {c.name: c.value for c in client.cookies.jar}
            ctype = r.headers.get("rblx-challenge-type", "")
            row = {"n": i, "status": r.status_code, "type": ctype,
                   "session": ".ROBLOSECURITY" in jars, "body": r.text[:120]}
            rows.append(row)
            print(f"  {i}/{args.attempts} {r.status_code} тип={ctype or '-':<12} "
                  f"{'СЕССИЯ' if row['session'] else ''} {r.text[:70]!r}", flush=True)
            if row["session"]:
                (Path("var") / f"session-{account['login']}.json").write_text(
                    json.dumps(jars, ensure_ascii=False, indent=2), encoding="utf-8")
                break
            if i < args.attempts:
                time.sleep(args.pause)

    Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    types = [r["type"] or "нет" for r in rows]
    print("\nтипы проверок по попыткам:", types, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
