"""Прохождение проверки чистым HTTP: proof-of-work и выдача токена captcha.

Найденный рабочий путь (замеры — docs/POW.md):

  1. вход с подписью SAI           -> 403 proofofwork;
  2. GET  pow-puzzle               -> N, A, T; решение A^(2^T) mod N;
  3. POST pow-puzzle               -> redemptionToken;
  4. POST challenge/v1/continue    -> 200, следующая ступень captchav2;
  5. POST v2/captcha {challenge_id} -> 200 {"redemption_token": ...}
     (сервер выдаёт токен сам, без интерактива);
  6. POST challenge/v1/continue с ним -> 200, цепочка закрыта;
  7. повтор входа с заголовками rblx-challenge-* -> 200 + сессия.

Если на шаге 5 приходит 403 с конфигурацией PerimeterX — значит платформа
требует интерактив; функция возвращает None, и вызывающий код может
переключиться на браузерный провайдер.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any

import httpx

from core.logging import get_logger, log
from flow.detect import Classification, classify_exchange

logr = get_logger(__name__)

DEFAULT_NONCE_URL = "https://apis.roblox.com/hba-service/v1/getServerNonce"
DEFAULT_POW_URL = "https://apis.roblox.com/proof-of-work-service/v1/pow-puzzle"
DEFAULT_CONTINUE_URL = "https://apis.roblox.com/challenge/v1/continue"
DEFAULT_CAPTCHA_URL = "https://apis.roblox.com/v2/captcha?urlLocale=en_us"


def _b64(value: Any) -> str:
    if isinstance(value, str):
        return base64.b64encode(value.encode()).decode()
    return base64.b64encode(json.dumps(value, separators=(",", ":")).encode()).decode()


def _decode_metadata(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    pad = "=" * (-len(str(raw)) % 4)
    for decode in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            return json.loads(decode(str(raw) + pad))
        except Exception:
            continue
    return {}


def solve_pow(artifacts: dict[str, Any], limit: int) -> tuple[str, float] | None:
    """Решение задачи. None, если сложность выше допустимой."""
    try:
        modulus = int(artifacts["N"])
        base = int(artifacts["A"])
        rounds = int(artifacts["T"])
    except (KeyError, TypeError, ValueError):
        return None
    if rounds <= 0 or rounds > limit:
        return None
    started = time.perf_counter()
    # A^(2^T) mod N: показатель 2^T, бинарное возведение в степень.
    result = pow(base, 1 << rounds, modulus)
    return str(result), time.perf_counter() - started


async def pass_challenge_http(
    client: httpx.AsyncClient,
    *,
    login_url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    metadata: Any,
    pow_limit: int = 5_000_000,
    continue_url: str = DEFAULT_CONTINUE_URL,
    captcha_url: str = DEFAULT_CAPTCHA_URL,
    pow_url: str = DEFAULT_POW_URL,
) -> tuple[httpx.Response, Classification] | None:
    """Провести цепочку проверки и повторить вход.

    Возвращает (ответ, разбор) при успешном прохождении цепочки и None,
    если платформа потребовала интерактив или что-то не сошлось.
    """
    meta = _decode_metadata(metadata)
    session_id = meta.get("sessionId")
    generic = (meta.get("sharedParameters") or {}).get("genericChallengeId")
    if not session_id:
        return None

    # 1. Задание и его решение.
    puzzle_resp = await client.get(pow_url, params={"sessionID": session_id},
                                   headers={**headers, "Accept": "application/json"})
    if puzzle_resp.status_code != 200:
        log(logr, 30, "задание PoW не выдано", code=puzzle_resp.status_code)
        return None
    artifacts = puzzle_resp.json().get("artifacts")
    if isinstance(artifacts, str):
        artifacts = json.loads(artifacts)
    solved = await asyncio.to_thread(solve_pow, artifacts or {}, pow_limit)
    if solved is None:
        log(logr, 30, "сложность PoW выше допустимой", t=artifacts.get("T"))
        return None
    solution, seconds = solved

    verdict = await client.post(pow_url, headers=headers,
                                json={"sessionID": session_id, "solution": solution})
    if verdict.status_code != 200:
        return None
    pow_row = verdict.json()
    if not pow_row.get("answerCorrect"):
        log(logr, 30, "решение PoW не принято", answer=pow_row.get("answerCorrect"))
        return None
    pow_token = pow_row.get("redemptionToken")
    if not pow_token:
        return None
    log(logr, 20, "PoW пройден", solve_s=round(seconds, 2), **{"pow.t": artifacts.get("T")})

    # 2. Продолжение цепочки: платформа сообщает следующую ступень.
    cont = await client.post(continue_url, headers=headers, json={
        "challengeId": generic or session_id,
        "challengeType": "proofofwork",
        "challengeMetadata": json.dumps({**meta, "redemptionToken": pow_token}),
    })
    if cont.status_code != 200:
        return None
    stage = cont.json()
    stage_id = stage.get("challengeId")
    stage_type = stage.get("challengeType") or "captchav2"
    if not stage_id:
        return None

    # 3. Токен второй ступени. Если сервер отвечает конфигурацией PerimeterX,
    #    интерактив неизбежен — уходим на браузерный провайдер.
    captcha = await client.post(captcha_url, headers=headers,
                                json={"challenge_id": stage_id})
    redeem = ""
    if captcha.status_code == 200:
        try:
            redeem = captcha.json().get("redemption_token", "")
        except Exception:
            redeem = ""
    if not redeem:
        log(logr, 30, "вторая ступень требует интерактива", code=captcha.status_code)
        return None

    # 4. Закрываем цепочку и повторяем вход.
    cont2 = await client.post(continue_url, headers=headers, json={
        "challengeId": stage_id,
        "challengeType": stage_type,
        "challengeMetadata": json.dumps({"redemptionToken": redeem}),
    })
    if cont2.status_code != 200:
        return None

    retry_headers = dict(headers)
    retry_headers.update({
        "rblx-challenge-id": str(stage_id),
        "rblx-challenge-type": stage_type,
        "rblx-challenge-metadata": _b64({"redemptionToken": redeem}),
    })
    resp = await client.post(login_url, headers=retry_headers, json=payload)
    return resp, classify_exchange(resp.status_code, resp.headers, resp.content)
