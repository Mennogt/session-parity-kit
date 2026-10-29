"""Полный поток: согласование токена → обмен → проверка → повтор → контроль.

Каждый внешний вызов: таймаут, классификация ошибки, лог с job_id,
сохранение сырого ответа при включённом захвате.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from pathlib import Path
from typing import Any

import httpx

from core.config import Settings
from core.logging import get_logger, log
from core.statuses import FlowOutcome, Status, Step
from challenge.base import SessionEstablished
from flow.detect import (
    ChallengePrompt,
    Classification,
    classify_exchange,
    dump_cookies,
)
from flow.http_challenge import pass_challenge_http
from flow.sai import build_sai
from obs import metrics

logr = get_logger("flow")

NETWORK_ERRORS = (
    httpx.ProxyError,
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
    httpx.ReadError,
    httpx.NetworkError,
)


def _backoff(attempt: int, base: float, cap: float) -> float:
    raw = min(cap, base * (2 ** (attempt - 1)))
    return raw * (0.5 + random.random() / 2)


class FlowClient:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self._timeout = httpx.Timeout(
            connect=settings.http_connect_timeout,
            read=settings.http_read_timeout,
            write=settings.http_read_timeout,
            pool=settings.http_connect_timeout,
        )

    # ---------- инфраструктура ----------

    def _headers(self, profile: Any, token: str | None = None) -> dict[str, str]:
        headers = {
            "User-Agent": profile.user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": profile.accept_language,
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": self.s.origin,
            "Referer": self.s.referer,
            # Подсказки клиента и метаданные выборки: без них платформа
            # отвечает проверкой без идентификатора, и повторить обмен
            # нечем (замерено, docs/POW.md).
            "sec-ch-ua": '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
        }
        headers.update(profile.extra_headers)
        if token:
            headers["X-CSRF-TOKEN"] = token
        return headers

    def _client(self, profile: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            proxy=profile.proxy_url or None,
            timeout=self._timeout,
            follow_redirects=False,
            cookies=profile.cookie_dict(),
        )

    def _capture(self, job_id: str, step: str, resp: httpx.Response) -> None:
        if not self.s.capture_fixtures:
            return
        out: Path = self.s.fixtures_dir
        out.mkdir(parents=True, exist_ok=True)
        record = {
            "job_id": job_id,
            "step": step,
            "status_code": resp.status_code,
            "headers": dict(resp.headers),
            "body": resp.text[:100_000],
            "at": time.time(),
        }
        (out / f"{job_id}-{step}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    async def _post(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        step: Step,
        job_id: str,
        profile: Any,
        payload: dict[str, Any] | None = None,
        token: str | None = None,
        retries: int = 2,
    ) -> tuple[httpx.Response | None, Status | None, str]:
        """POST с ретраями на сетевые ошибки и лимиты. Возвращает (resp, error, detail)."""
        last_detail = ""
        for attempt in range(1, retries + 2):
            started = time.perf_counter()
            try:
                resp = await client.post(
                    url,
                    headers=self._headers(profile, token),
                    content=json.dumps(payload, ensure_ascii=False) if payload is not None else None,
                )
            except NETWORK_ERRORS as exc:
                elapsed = (time.perf_counter() - started) * 1000
                metrics.proxy_fail_total.labels(reason=type(exc).__name__).inc()
                log(
                    logr, 30, "сетевой отказ",
                    step=str(step), attempt=attempt, elapsed_ms=round(elapsed, 1),
                    error=type(exc).__name__, url=url,
                )
                last_detail = f"{type(exc).__name__}: {exc}"
                if attempt > retries:
                    return None, Status.PROXY_ERROR, last_detail
                await asyncio.sleep(_backoff(attempt, self.s.backoff_base, self.s.backoff_max))
                continue

            elapsed = (time.perf_counter() - started) * 1000
            self._capture(job_id, f"{step}-a{attempt}", resp)
            log(
                logr, 20, "ответ получен",
                step=str(step), attempt=attempt, code=resp.status_code,
                elapsed_ms=round(elapsed, 1), bytes=len(resp.content),
            )

            if resp.status_code == 429:
                retry_after = _retry_after_of(resp)
                if attempt <= retries:
                    wait = retry_after if retry_after is not None else _backoff(
                        attempt, self.s.backoff_base, self.s.backoff_max
                    )
                    log(logr, 30, "лимит частоты, пауза", step=str(step), wait_s=round(wait, 2))
                    await asyncio.sleep(min(wait, self.s.backoff_max))
                    last_detail = "лимит частоты"
                    continue
                return resp, Status.RATE_LIMITED, last_detail or "лимит частоты"

            if resp.status_code >= 500 and attempt <= retries:
                last_detail = f"платформа ответила {resp.status_code}"
                await asyncio.sleep(_backoff(attempt, self.s.backoff_base, self.s.backoff_max))
                continue

            return resp, None, ""

        return None, Status.PROXY_ERROR, last_detail or "исчерпаны попытки"

    # ---------- шаги ----------

    async def _csrf(self, client: httpx.AsyncClient, profile: Any, job_id: str, ident: str, secret: str) -> tuple[str | None, Status | None, str]:
        # Токен запроса выдаётся и на заведомо неверную пару. Настоящие
        # учётные данные здесь не нужны: запрос без подписи SAI ставит
        # сессию в состояние «проверка без идентификатора» и мешает
        # последующему обмену.
        resp, err, detail = await self._post(
            client,
            f"{self.s.auth_base}/v2/login",
            step=Step.CSRF,
            job_id=job_id,
            profile=profile,
            payload={"ctype": "Username", "cvalue": "spk_csrf_probe", "password": "x"},
        )
        if err is not None:
            return None, err, detail
        assert resp is not None
        token = resp.headers.get("x-csrf-token") or resp.headers.get("X-CSRF-TOKEN")
        if token:
            log(logr, 20, "токен запроса получен", step="csrf", token_len=len(token))
            return token, None, ""
        if resp.status_code in (200, 204):
            # Согласование не требуется — идём без токена.
            return None, None, "платформа не потребовала токен"
        cls = classify_exchange(resp.status_code, resp.headers, resp.content)
        if cls.status is not None:
            return None, cls.status, cls.detail
        return None, Status.INTERNAL_ERROR, (
            f"403 без заголовка x-csrf-token (тело: {resp.text[:200]!r})"
        )

    async def _exchange(
        self,
        client: httpx.AsyncClient,
        profile: Any,
        job_id: str,
        ident: str,
        secret: str,
        token: str | None,
        challenge: ChallengePrompt | None = None,
        challenge_token: str | None = None,
        step: Step = Step.EXCHANGE,
        sai: dict[str, Any] | None = None,
    ) -> tuple[httpx.Response | None, Classification]:
        payload: dict[str, Any] = {
            "ctype": "Username",
            "cvalue": ident,
            "password": secret,
        }
        # Подпись переводит проверку из интерактивной в вычислимую
        # (docs/POW.md). Без неё платформа отвечает капчей PerimeterX.
        if sai is not None:
            payload["secureAuthenticationIntent"] = sai
        if challenge is not None and challenge_token is not None:
            payload["captchaId"] = challenge.captcha_id
            payload["captchaToken"] = challenge_token
            payload["captchaProvider"] = challenge.provider

        resp, err, detail = await self._post(
            client,
            f"{self.s.auth_base}/v2/login",
            step=step,
            job_id=job_id,
            profile=profile,
            payload=payload,
            token=token,
        )
        if err is not None or resp is None:
            return None, Classification(status=err or Status.PROXY_ERROR, detail=detail)
        return resp, classify_exchange(resp.status_code, resp.headers, resp.content)

    async def _prime(self, client: httpx.AsyncClient, profile: Any, job_id: str) -> None:
        """Прогрев: открыть страницу входа и главную, как это делает браузер.

        Даёт платформе историю обращений и cookie гостя; без этого обмен
        парой может прийти с требованием проверки без идентификатора.
        """
        for url in (f"{self.s.origin}/login", f"{self.s.origin}/home"):
            try:
                resp = await client.get(
                    url,
                    headers={
                        "User-Agent": profile.user_agent,
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": profile.accept_language,
                        "Sec-Fetch-Dest": "document",
                        "Sec-Fetch-Mode": "navigate",
                        "Sec-Fetch-Site": "none",
                        "Sec-Fetch-User": "?1",
                        "Upgrade-Insecure-Requests": "1",
                    },
                )
                self._capture(job_id, "prime", resp)
                log(logr, 20, "прогрев", url=url, code=resp.status_code,
                    bytes=len(resp.content), cookies=len(client.cookies.jar))
            except NETWORK_ERRORS as exc:
                metrics.proxy_fail_total.labels(reason=f"prime.{type(exc).__name__}").inc()
                log(logr, 30, "прогрев не удался", url=url, error=type(exc).__name__)

    async def _verify(self, client: httpx.AsyncClient, profile: Any, job_id: str) -> FlowOutcome:
        started = time.perf_counter()
        try:
            resp = await client.get(
                self.s.verify_url,
                headers={
                    "User-Agent": profile.user_agent,
                    "Accept": "application/json",
                    "Accept-Language": profile.accept_language,
                    "Origin": self.s.origin,
                    "Referer": f"{self.s.origin}/home",
                },
                timeout=httpx.Timeout(
                    connect=self.s.http_connect_timeout,
                    read=self.s.verify_timeout,
                    write=self.s.http_read_timeout,
                    pool=self.s.http_connect_timeout,
                ),
            )
        except NETWORK_ERRORS as exc:
            metrics.proxy_fail_total.labels(reason=type(exc).__name__).inc()
            log(logr, 30, "контрольный запрос не прошёл", step="verify", error=type(exc).__name__)
            return FlowOutcome(Status.PROXY_ERROR, Step.VERIFY, f"{type(exc).__name__}: {exc}")

        elapsed = (time.perf_counter() - started) * 1000
        self._capture(job_id, "verify", resp)
        log(logr, 20, "контрольный запрос", step="verify", code=resp.status_code,
            elapsed_ms=round(elapsed, 1))

        if resp.status_code == 200:
            try:
                ident = resp.json()
            except json.JSONDecodeError:
                ident = {"raw": resp.text[:200]}
            log(logr, 20, "сессия подтверждена", step="verify", identity=ident)
            return FlowOutcome(
                Status.SUCCESS,
                Step.VERIFY,
                "сессия подтверждена контрольным запросом",
                cookies=dump_cookies(resp.headers),
                attempts=1,
            )
        if resp.status_code in (401, 403):
            return FlowOutcome(
                Status.INVALID_SESSION,
                Step.VERIFY,
                f"контрольный запрос вернул {resp.status_code}: {resp.text[:200]}",
            )
        if resp.status_code == 429:
            return FlowOutcome(
                Status.RATE_LIMITED,
                Step.VERIFY,
                "лимит частоты на контрольном запросе",
                retry_after=_retry_after_of(resp),
            )
        return FlowOutcome(
            Status.INTERNAL_ERROR,
            Step.VERIFY,
            f"неожиданный код контрольного запроса {resp.status_code}",
        )

    # ---------- сценарий ----------

    async def execute(
        self,
        profile: Any,
        ident: str,
        secret: str,
        job_id: str,
        solve_challenge: Any,
    ) -> tuple[FlowOutcome, dict[str, Any]]:
        """Полный прогон с замером длительности на любом исходе."""
        started = time.perf_counter()
        outcome: FlowOutcome | None = None
        cookies: dict[str, Any] = {}
        try:
            outcome, cookies = await self._execute(
                profile, ident, secret, job_id, solve_challenge
            )
            return outcome, cookies
        finally:
            elapsed = time.perf_counter() - started
            metrics.login_flow_seconds.observe(elapsed)
            if outcome is not None and outcome.status is not Status.SUCCESS:
                metrics.step_errors_total.labels(
                    step=str(outcome.step), status=str(outcome.status)
                ).inc()

    async def _execute(
        self,
        profile: Any,
        ident: str,
        secret: str,
        job_id: str,
        solve_challenge: Any,
    ) -> tuple[FlowOutcome, dict[str, Any]]:
        """Полный прогон. Возвращает итог и обновлённую cookie-банку профиля."""
        started = time.perf_counter()
        challenge_passed = False
        solve_seconds = 0.0

        async with self._client(profile) as client:
            if self.s.prime_session:
                await self._prime(client, profile, job_id)

            token, err, detail = await self._csrf(client, profile, job_id, ident, secret)
            if err is not None:
                profile.absorb(client.cookies)
                return FlowOutcome(err, Step.CSRF, detail), profile.cookies

            sai = None
            if self.s.sai_enabled:
                sai = await build_sai(client, self.s.hba_nonce_url,
                                      self._headers(profile, token))
                if sai is None:
                    log(logr, 30, "подпись входа не собрана, работаем без неё", step="csrf")

            resp, cls = await self._exchange(client, profile, job_id, ident, secret, token,
                                             sai=sai)

            # Повторное согласование токена, если платформа его инвалидировала.
            if resp is not None and cls.csrf_refresh_needed:
                fresh = resp.headers.get("x-csrf-token")
                if not fresh:
                    profile.absorb(client.cookies)
                    return (
                        FlowOutcome(
                            Status.INTERNAL_ERROR,
                            Step.CSRF,
                            "токен инвалидирован, новый не выдан",
                        ),
                        profile.cookies,
                    )
                log(logr, 20, "токен обновлён после отказа", step="csrf")
                token = fresh
                resp, cls = await self._exchange(client, profile, job_id, ident, secret, token,
                                                 sai=sai)

            if resp is None:
                profile.absorb(client.cookies)
                return (
                    FlowOutcome(cls.status or Status.PROXY_ERROR, Step.EXCHANGE, cls.detail),
                    profile.cookies,
                )

            # Узел проверки.
            if cls.challenge is not None and self.s.http_challenge_enabled:
                # Сначала чистый HTTP: PoW и токен второй ступени. Если
                # платформа требует интерактив, вернётся None.
                payload = {"ctype": "Username", "cvalue": ident, "password": secret}
                if sai is not None:
                    payload["secureAuthenticationIntent"] = sai
                http_try = await pass_challenge_http(
                    client,
                    login_url=f"{self.s.auth_base}/v2/login",
                    headers=self._headers(profile, token),
                    payload=payload,
                    metadata=cls.challenge.raw_metadata,
                    pow_limit=self.s.pow_difficulty_limit,
                    continue_url=self.s.challenge_continue_url,
                    captcha_url=self.s.captcha_url,
                    pow_url=self.s.pow_puzzle_url,
                )
                if http_try is not None:
                    resp, cls = http_try
                    challenge_passed = cls.challenge is None
                    log(logr, 20, "проверка пройдена по HTTP", step="challenge",
                        code=getattr(resp, "status_code", None))

            if cls.challenge is not None:
                log(logr, 20, "требуется узел проверки", step="challenge",
                    captcha_id=cls.challenge.captcha_id)
                solve_started = time.perf_counter()
                try:
                    challenge_token = await solve_challenge(cls.challenge, profile)
                except SessionEstablished as established:
                    # Проверка завершила вход в браузере: cookie уже в профиле,
                    # повторный обмен не нужен — сразу контрольный запрос.
                    solve_seconds = time.perf_counter() - solve_started
                    metrics.captcha_solve_seconds.observe(solve_seconds)
                    log(logr, 20, "сессия установлена в браузере",
                        step="challenge", solve_s=round(solve_seconds, 2),
                        detail=str(established)[:200])
                    profile.absorb(client.cookies)
                    outcome = await self._verify(client, profile, job_id)
                    outcome.challenge_passed = True
                    return outcome, profile.cookies
                except Exception as exc:  # провайдер обязан быть безопасным
                    solve_seconds = time.perf_counter() - solve_started
                    metrics.captcha_solve_seconds.observe(solve_seconds)
                    profile.absorb(client.cookies)
                    log(logr, 40, "узел проверки не пройден", step="challenge",
                        error=type(exc).__name__, detail=str(exc)[:300])
                    return (
                        FlowOutcome(
                            Status.CHALLENGE_FAILED,
                            Step.CHALLENGE,
                            f"{type(exc).__name__}: {exc}",
                        ),
                        profile.cookies,
                    )
                solve_seconds = time.perf_counter() - solve_started
                metrics.captcha_solve_seconds.observe(solve_seconds)
                log(logr, 20, "узел проверки пройден", step="challenge",
                    solve_s=round(solve_seconds, 2))

                resp, cls = await self._exchange(
                    client,
                    profile,
                    job_id,
                    ident,
                    secret,
                    token,
                    challenge=cls.challenge,
                    challenge_token=challenge_token,
                    step=Step.RETRY,
                )
                if resp is None:
                    profile.absorb(client.cookies)
                    return (
                        FlowOutcome(cls.status or Status.PROXY_ERROR, Step.RETRY, cls.detail),
                        profile.cookies,
                    )
                again = cls.challenge
                if again is not None:
                    profile.absorb(client.cookies)
                    return (
                        FlowOutcome(
                            Status.CHALLENGE_FAILED,
                            Step.RETRY,
                            f"проверка запрошена повторно: {again.captcha_id}",
                        ),
                        profile.cookies,
                    )
                challenge_passed = True

            if cls.status is not None:
                profile.absorb(client.cookies)
                return (
                    FlowOutcome(
                        cls.status,
                        Step.RETRY if challenge_passed else Step.EXCHANGE,
                        cls.detail,
                        attempts=1,
                        retry_after=cls.retry_after,
                    ),
                    profile.cookies,
                )

            # 200 без ошибки: cookie получены, подтверждаем отдельным запросом.
            set_cookie_names = [c["name"] for c in dump_cookies(resp.headers)]
            log(logr, 20, "обмен принят", step="exchange", cookies=set_cookie_names)
            outcome = await self._verify(client, profile, job_id)
            profile.absorb(client.cookies)

            if outcome.status is Status.SUCCESS:
                outcome.cookies = dump_cookies(resp.headers) or [
                    {
                        "name": c.name,
                        "value": c.value,
                        "domain": c.domain or "",
                        "path": c.path or "/",
                        "httpOnly": False,
                    }
                    for c in client.cookies.jar
                ]
                outcome.challenge_passed = challenge_passed

            elapsed_ms = int((time.perf_counter() - started) * 1000)
            log(
                logr, 20 if outcome.status is Status.SUCCESS else 30,
                "поток завершён", status=str(outcome.status), step=str(outcome.step),
                elapsed_ms=elapsed_ms, challenge_passed=challenge_passed,
                solve_s=round(solve_seconds, 2),
            )
            return outcome, profile.cookies


def _retry_after_of(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw.strip()))
    except ValueError:
        return None
