"""Proof-of-work проверка Roblox: прохождение чистым HTTP.

С правильной подписью `secureAuthenticationIntent` (ECDSA P-256 над строкой
`clientPublicKey|timestamp|serverNonce`) платформа меняет тип проверки с
интерактивной PerimeterX на `proofofwork`. Это арифметика, а не капча:
нужно найти значение, хеш которого удовлетворяет условию.

Схема:
  1. nonce от hba-service, подпись, вход по паре;
  2. 403 + `rblx-challenge-id` + `rblx-challenge-type: proofofwork`;
  3. `POST apis.roblox.com/v2/captcha` с `challenge_id` — параметры задания;
  4. решение, отправка решения, получение `redemptionToken`;
  5. повторный вход с заголовками `rblx-challenge-*`.

    python -m probes.pow_flow --account <login>
"""

from __future__ import annotations

import argparse
import base64
import hashlib
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
H_JSON = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://www.roblox.com",
    "Referer": "https://www.roblox.com/login",
    "sec-ch-ua": '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
}
H_NAV = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "sec-ch-ua": '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-site": "none",
    "sec-fetch-mode": "navigate",
    "sec-fetch-dest": "document",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
}
LOGIN = "https://auth.roblox.com/v2/login"
CAPTCHA = "https://apis.roblox.com/v2/captcha?urlLocale=en_us"


def b64json(value: str) -> dict:
    pad = "=" * (-len(value) % 4)
    for dec in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            return json.loads(dec(value + pad))
        except Exception:
            continue
    return {}


def make_sai(nonce: str) -> tuple[dict, bytes]:
    key = ec.generate_private_key(ec.SECP256R1())
    spki = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    pub = base64.b64encode(spki).decode()
    ts = int(time.time())
    payload = f"{pub}|{ts}|{nonce}".encode()
    der = key.sign(payload, ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der)
    sig = base64.b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode()
    return {
        "clientPublicKey": pub,
        "clientEpochTimestamp": ts,
        "serverNonce": nonce,
        "saiSignature": sig,
    }, payload


def solve_pow(spec: dict, deadline: float = 120.0) -> dict | None:
    """Перебор по описанию задания. Платформа может задавать разные схемы."""
    print(f"   параметры задания: {json.dumps(spec, ensure_ascii=False)[:400]}")
    prefix = spec.get("prefix") or spec.get("salt") or spec.get("challenge") or ""
    difficulty = int(spec.get("difficulty") or spec.get("bits") or 0)
    target = (spec.get("target") or "").lower()
    algorithm = (spec.get("algorithm") or spec.get("algo") or "sha256").lower()
    field = spec.get("field") or spec.get("answerField") or "answer"

    if not (prefix or target) and not difficulty:
        return None

    started = time.perf_counter()
    counter = 0
    while time.perf_counter() - started < deadline:
        text = f"{prefix}{counter}"
        digest = hashlib.new(algorithm, text.encode()).hexdigest()
        ok = False
        if target:
            ok = digest == target or digest.startswith(target)
        elif difficulty:
            ok = digest.startswith("0" * difficulty)
        if ok:
            elapsed = time.perf_counter() - started
            print(f"   решение найдено: counter={counter}, {algorithm}={digest[:24]}…, "
                  f"{elapsed:.2f} с, попыток {counter + 1}")
            return {field: str(counter), "solution": str(counter), "hash": digest,
                    "prefix": prefix, "difficulty": difficulty, "seconds": round(elapsed, 2)}
        counter += 1
    print("   решение не найдено за отведённое время")
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="<login>")
    ap.add_argument("--proxy-url", default=None)
    ap.add_argument("--out", default="var/pow-flow.json")
    args = ap.parse_args()

    accounts = json.loads(Path("var/accounts.json").read_text(encoding="utf-8"))
    account = next(a for a in accounts if a["login"].lower() == args.account.lower())
    report: dict = {"account": account["login"], "steps": []}

    with httpx.Client(proxy=args.proxy_url, timeout=60, follow_redirects=False) as client:
        # Прогрев страницы: гостевые cookie и токен запроса.
        client.get("https://www.roblox.com/login", headers=H_NAV)
        r0 = client.post(LOGIN, headers=H_JSON,
                         json={"ctype": "Username", "cvalue": "probe_zz", "password": "x"})
        csrf = r0.headers.get("x-csrf-token") or ""
        headers = dict(H_JSON)
        if csrf:
            headers["X-CSRF-TOKEN"] = csrf
        print(f"csrf: {'есть' if csrf else 'нет'}")

        nonce = client.get("https://apis.roblox.com/hba-service/v1/getServerNonce",
                           headers={"User-Agent": UA, "Accept": "application/json",
                                    "Origin": "https://www.roblox.com"}).json()
        sai, payload = make_sai(str(nonce))
        print(f"nonce: {nonce}; подпись по '{'pub|ts|nonce'}'")

        body = {"ctype": "Username", "cvalue": account["login"],
                "password": account["password"], "secureAuthenticationIntent": sai}
        r1 = client.post(LOGIN, headers=headers, json=body)
        meta_raw = r1.headers.get("rblx-challenge-metadata", "")
        ch_id = r1.headers.get("rblx-challenge-id", "")
        ch_type = r1.headers.get("rblx-challenge-type", "")
        meta = b64json(meta_raw)
        print(f"1) вход: {r1.status_code}; тип проверки: {ch_type!r}")
        print(f"   метаданные: {json.dumps(meta, ensure_ascii=False)[:300]}")
        report["login"] = {"status": r1.status_code, "type": ch_type, "meta": meta,
                          "body": r1.text[:200]}

        session_id = meta.get("sessionId") or meta.get("challengeId") or ch_id
        r2 = client.post(CAPTCHA, headers=headers, json={"challenge_id": session_id})
        print(f"2) /v2/captcha: {r2.status_code}")
        print(f"   тело: {r2.text[:600]!r}")
        report["captcha"] = {"status": r2.status_code, "body": r2.text[:1500]}

        try:
            spec = r2.json()
        except Exception:
            spec = {}
        report["spec"] = spec

        solution = solve_pow(spec) if spec else None
        if not solution:
            print("3) решение не получено — протокол требует уточнения")
            Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
            return 0

        report["solution"] = solution
        # Отправляем решение туда же, где брали задание.
        for attempt_payload in (
            {"challenge_id": session_id, **solution},
            {"challenge_id": session_id, "solution": solution.get("solution", "")},
        ):
            r3 = client.post(CAPTCHA, headers=headers, json=attempt_payload)
            print(f"4) отправка решения: {r3.status_code} {r3.text[:300]!r}")
            report.setdefault("submissions", []).append(
                {"status": r3.status_code, "body": r3.text[:600]}
            )
            try:
                answer = r3.json()
            except Exception:
                answer = {}
            token = (answer.get("redemptionToken") or answer.get("token")
                     or answer.get("solution"))
            if token:
                print(f"   redemptionToken получен, длина {len(str(token))}")
                patched = dict(meta)
                patched["redemptionToken"] = token
                payload_meta = base64.b64encode(
                    json.dumps(patched).encode()
                ).decode()
                retry_headers = dict(headers)
                retry_headers.update({
                    "rblx-challenge-id": ch_id or session_id,
                    "rblx-challenge-type": ch_type or "proofofwork",
                    "rblx-challenge-metadata": payload_meta,
                    "x-retry-attempt": "1",
                })
                r4 = client.post(LOGIN, headers=retry_headers, json=body)
                session = [c.name for c in client.cookies.jar]
                print(f"5) повторный вход: {r4.status_code} {r4.text[:200]}")
                print(f"   cookie: {session}")
                report["retry"] = {"status": r4.status_code, "body": r4.text[:400],
                                  "cookies": session}
                if ".ROBLOSECURITY" in session:
                    jar = {c.name: c.value for c in client.cookies.jar}
                    (Path("var") / f"session-{account['login']}.json").write_text(
                        json.dumps(jar, ensure_ascii=False, indent=2), encoding="utf-8")
                    print("   СЕССИЯ ПОЛУЧЕНА ЧЕРЕЗ HTTP")
                break

    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
