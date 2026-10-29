"""Подпись входа (SAI) вместо проверки.

Браузер отправляет в теле входа:

    "secureAuthenticationIntent": {
        "clientPublicKey":   <SPKI DER, base64>,
        "clientEpochTimestamp": <unix>,
        "serverNonce":       <16 символов>,
        "saiSignature":      <ECDSA P-256, raw r||s, base64>
    }

Nonce выдаёт `apis.roblox.com/hba-service/v1/getServerNonce`.
Проверяем гипотезу: платформа требует подпись для «доверенного» входа, и
именно её отсутствие в наших прежних запросах включало проверку.

Скрипт перебирает варианты подписываемого сообщения и сравнивает ответы.
Пароль при каждом варианте настоящий, но вариантов немного.

    python -m probes.sai_flow --account 3
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import time
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
BASE = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://www.roblox.com",
    "Referer": "https://www.roblox.com/login",
    "Content-Type": "application/json;charset=UTF-8",
}
VAR = Path("var")


def make_key() -> tuple[str, object]:
    key = ec.generate_private_key(ec.SECP256R1())
    spki = key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return base64.b64encode(spki).decode(), key


def sign_raw(key, message: bytes) -> str:
    """ECDSA P-256 SHA-256, raw r||s — так подписывает WebCrypto."""
    der = key.sign(message, ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der)
    raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return base64.b64encode(raw).decode()


async def get_nonce(client: httpx.AsyncClient) -> str:
    r = await client.get(
        "https://apis.roblox.com/hba-service/v1/getServerNonce?urlLocale=en_us",
        headers={"User-Agent": UA, "Accept": "application/json",
                 "Origin": "https://www.roblox.com",
                 "Referer": "https://www.roblox.com/login"},
    )
    try:
        return str(r.json())
    except Exception:
        text = r.text.strip().strip('"')
        return text


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, default=3)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--out", default="var/sai-flow.json")
    args = ap.parse_args()

    accounts = json.loads((VAR / "accounts.json").read_text(encoding="utf-8"))
    account = accounts[args.account]
    proxy = None
    if args.port:
        from probes.proxy_check import proxy_url

        proxy = proxy_url(args.port)

    report: list[dict] = []

    async with httpx.AsyncClient(proxy=proxy, timeout=40, follow_redirects=False,
                                 headers=dict(BASE)) as client:
        await client.get("https://www.roblox.com/login",
                         headers={**BASE, "Accept": "text/html"})
        r = await client.post("https://auth.roblox.com/v2/login",
                              json={"ctype": "Username", "cvalue": "probe_zz_none", "password": "x"})
        token = r.headers.get("x-csrf-token")
        client.headers["X-CSRF-TOKEN"] = token or ""

        nonce = await get_nonce(client)
        print(f"nonce: {nonce!r}")

        pub, key = make_key()
        ts = int(time.time())

        variants = {
            "nonce": nonce.encode(),
            "nonce+ts": nonce.encode() + str(ts).encode(),
            "ts+nonce": str(ts).encode() + nonce.encode(),
            "pub+nonce+ts": pub.encode() + nonce.encode() + str(ts).encode(),
            "json": json.dumps({"serverNonce": nonce, "clientEpochTimestamp": ts},
                               separators=(",", ":")).encode(),
        }

        for name, message in variants.items():
            intent = {
                "clientPublicKey": pub,
                "clientEpochTimestamp": ts,
                "serverNonce": nonce,
                "saiSignature": sign_raw(key, message),
            }
            body = {"ctype": "Username", "cvalue": account["login"],
                    "password": account["password"],
                    "secureAuthenticationIntent": intent}
            resp = await client.post("https://auth.roblox.com/v2/login", json=body)
            ch = {k: v for k, v in resp.headers.items() if k.startswith("rblx-challenge")}
            report.append({"variant": name, "status": resp.status_code,
                           "body": resp.text[:200], "challenge": bool(ch)})
            print(f"  {name:<16} {resp.status_code} "
                  f"{'ПРОВЕРКА' if ch else 'без проверки'} {resp.text[:110]!r}")
            if resp.status_code == 200:
                jar = {c.name: c.value for c in client.cookies.jar}
                if ".ROBLOSECURITY" in jar:
                    print("   СЕССИЯ ПОЛУЧЕНА")
                    (VAR / f"session-{account['login']}.json").write_text(
                        json.dumps(jar, ensure_ascii=False, indent=2), encoding="utf-8")
                    break
            await asyncio.sleep(3)

    (VAR / args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== итог ===")
    for r in report:
        print(f"  {r['variant']:<16} {r['status']} проверка={r['challenge']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
