"""Сложность PoW как индикатор риска адреса.

Платформа выдаёт задание вида `A^(2^T) mod N`. Малое `T` (сотни тысяч)
решается за секунды и принимается; большое (десятки миллионов) — это уже
фактический отказ: задание живёт секунды, а решается минутами.

Скрипт по списку прокси смотрит `T` и тип проверки, не решая задание.

    python -u -m probes.pow_difficulty --urls var/mobile-urls.txt --count 6
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
PUZZLE = "https://apis.roblox.com/proof-of-work-service/v1/pow-puzzle"
KEY_FILE = Path("var/sai-key.pem")


def load_key():
    if KEY_FILE.exists():
        return serialization.load_pem_private_key(KEY_FILE.read_bytes(), password=None)
    key = ec.generate_private_key(ec.SECP256R1())
    KEY_FILE.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    return key


def b64json(v: str) -> dict:
    pad = "=" * (-len(v) % 4)
    for dec in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            return json.loads(dec(v + pad))
        except Exception:
            continue
    return {}


def probe(proxy: str | None, account: dict, key) -> dict:
    row: dict = {"proxy": (proxy or "прямой").split("@")[-1]}
    try:
        with httpx.Client(proxy=proxy, timeout=60, follow_redirects=False) as c:
            c.get("https://www.roblox.com/login", headers={**H, "Accept": "text/html"})
            r0 = c.post(LOGIN, headers=H,
                        json={"ctype": "Username", "cvalue": "probe_zz", "password": "x"})
            headers = dict(H)
            if r0.headers.get("x-csrf-token"):
                headers["X-CSRF-TOKEN"] = r0.headers["x-csrf-token"]
            nonce = c.get("https://apis.roblox.com/hba-service/v1/getServerNonce",
                          headers={"User-Agent": UA}).json()
            spki = key.public_key().public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo)
            pub = base64.b64encode(spki).decode()
            ts = int(time.time())
            der = key.sign(f"{pub}|{ts}|{nonce}".encode(), ec.ECDSA(hashes.SHA256()))
            r, s = utils.decode_dss_signature(der)
            sai = {"clientPublicKey": pub, "clientEpochTimestamp": ts, "serverNonce": nonce,
                   "saiSignature": base64.b64encode(
                       r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode()}
            body = {"ctype": "Username", "cvalue": account["login"],
                    "password": account["password"], "secureAuthenticationIntent": sai}
            r1 = c.post(LOGIN, headers=headers, json=body)
            row["status"] = r1.status_code
            row["type"] = r1.headers.get("rblx-challenge-type", "")
            meta = b64json(r1.headers.get("rblx-challenge-metadata", ""))
            sid = meta.get("sessionId")
            if not sid:
                row["note"] = r1.text[:80]
                return row
            if r1.headers.get("x-csrf-token"):
                headers["X-CSRF-TOKEN"] = r1.headers["x-csrf-token"]
            p = c.get(PUZZLE, headers={"User-Agent": UA}, params={"sessionID": sid})
            row["puzzle_status"] = p.status_code
            try:
                payload = p.json()
                art = payload.get("artifacts")
                if isinstance(art, str):
                    art = json.loads(art)
                row["T"] = int(art.get("T") or 0)
                row["N_digits"] = len(str(art.get("N") or ""))
            except Exception:
                row["note"] = p.text[:80]
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {str(exc)[:70]}"
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls", default="var/mobile-urls.txt")
    ap.add_argument("--count", type=int, default=6)
    ap.add_argument("--account", default="<login>")
    ap.add_argument("--out", default="var/pow-difficulty.json")
    args = ap.parse_args()

    accounts = json.loads(Path("var/accounts.json").read_text(encoding="utf-8"))
    account = next(a for a in accounts if a["login"].lower() == args.account.lower())
    urls = [u for u in Path(args.urls).read_text(encoding="utf-8").splitlines() if u.strip()]
    key = load_key()

    rows = [probe(None, account, key)]
    print(f"прямой IP: тип={rows[0].get('type')} T={rows[0].get('T')}", flush=True)
    for url in urls[: args.count]:
        row = probe(url, account, key)
        rows.append(row)
        print(f"  {row['proxy']:<16} тип={row.get('type', '-'):<14} "
              f"T={row.get('T', '-')} {row.get('error', '')}", flush=True)
        time.sleep(2)

    Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    small = [r for r in rows if isinstance(r.get("T"), int) and 0 < r["T"] <= 1_000_000]
    print(f"\nадресов с лёгким заданием: {len(small)} из {len(rows)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
