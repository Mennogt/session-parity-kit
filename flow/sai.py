"""Подпись входа `secureAuthenticationIntent`.

Платформа различает клиентов по наличию этой подписи: без неё выдаёт
интерактивную капчу PerimeterX, с ней — вычислимую задачу proof-of-work.

Формат: ECDSA P-256, SHA-256, сообщение

    clientPublicKey|clientEpochTimestamp|serverNonce

через вертикальные черты, ключ эфемерный, подпись — raw r||s в base64.
"""

from __future__ import annotations

import base64
import time

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils


def make_sai(nonce: str, *, timestamp: int | None = None) -> dict[str, object]:
    """Собрать подпись для указанного nonce."""
    key = ec.generate_private_key(ec.SECP256R1())
    spki = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    public_key = base64.b64encode(spki).decode()
    stamp = int(timestamp if timestamp is not None else time.time())
    der = key.sign(f"{public_key}|{stamp}|{nonce}".encode(), ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der)
    signature = base64.b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode()
    return {
        "clientPublicKey": public_key,
        "clientEpochTimestamp": stamp,
        "serverNonce": nonce,
        "saiSignature": signature,
    }


async def fetch_nonce(client: httpx.AsyncClient, url: str, headers: dict[str, str]) -> str:
    """Получить серверный nonce (plain JSON-строка)."""
    resp = await client.get(url, headers={**headers, "Accept": "application/json"})
    resp.raise_for_status()
    value = resp.json()
    return str(value)


async def build_sai(
    client: httpx.AsyncClient, nonce_url: str, headers: dict[str, str]
) -> dict[str, object] | None:
    """Подпись под текущий момент; None, если nonce получить не удалось."""
    try:
        nonce = await fetch_nonce(client, nonce_url, headers)
    except Exception:
        return None
    return make_sai(nonce)
