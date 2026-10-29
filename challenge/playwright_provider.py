"""Прохождение узла проверки в реальном браузерном контексте.

Контекст живёт внутри профиля: тот же прокси, тот же User-Agent, та же
cookie-банка. Токен провайдера добывается послойно, тремя стратегиями,
в порядке от самой точной к самой запасной:

1. hook на объект провайдера в странице — перехват колбэка завершения;
2. перехват ответа сети, в котором провайдер отдаёт токен;
3. перехват сообщений iframe → родительская страница.

Каждая попытка логируется. Если ни одна не дала токен, ошибка содержит
перечень опробованных стратегий и последнюю наблюдаемую активность —
этого достаточно, чтобы понять, где именно разошлось поведение.

Уборка гарантирована: контекст и браузер закрываются в finally.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import time
from typing import Any

from challenge.base import (
    ChallengeError,
    ChallengeTimeout,
    ProviderUnavailable,
    SessionEstablished,
)
from core.config import Settings
from core.logging import get_logger, log
from flow.detect import ChallengePrompt
from obs import metrics
from profiles.model import Profile

logr = get_logger("challenge")

TOKEN_KEYS = ("token", "captchaToken", "captcha_token", "arkose_token")
TOKEN_URL_MARKERS = ("/fc/gt2/", "/fc/gc", "/fc/ca", "/v2/")

# Перехват в странице: и объект провайдера, и postMessage, и глобальные
# присваивания. Всё складывается в window.__spk, страница не ломается.
INIT_SCRIPT = r"""
(() => {
  window.__spk = { tokens: [], events: [], errors: [] };
  const push = (t, src) => {
    if (typeof t === 'string' && t.length > 20) {
      window.__spk.tokens.push({ token: t, src, at: Date.now() });
    }
  };
  const scan = (obj, src) => {
    if (!obj || typeof obj !== 'object') return;
    for (const k of ['token', 'captchaToken', 'arkose_token', 'sessionToken']) {
      if (typeof obj[k] === 'string') push(obj[k], src + '.' + k);
    }
  };
  // 1. postMessage: провайдер общается с родителем сообщениями.
  const origAdd = window.addEventListener;
  window.addEventListener = function (type, fn, opts) {
    if (type === 'message' && typeof fn === 'function') {
      return origAdd.call(this, type, function (ev) {
        try {
          if (ev && ev.data) {
            const d = typeof ev.data === 'string' ? ev.data : ev.data;
            scan(d, 'message');
            if (typeof d === 'string') {
              try { scan(JSON.parse(d), 'message.json'); } catch (e) {}
            }
          }
        } catch (e) { window.__spk.errors.push(String(e)); }
        return fn.apply(this, arguments);
      }, opts);
    }
    return origAdd.apply(this, arguments);
  };
  // 2. Перехват конструктора провайдера: экземпляр отдаёт токен в конфиге.
  let realCtor = undefined;
  Object.defineProperty(window, 'FunCaptcha', {
    configurable: true,
    get() { return realCtor; },
    set(fn) {
      if (typeof fn !== 'function') { realCtor = fn; return; }
      realCtor = new Proxy(fn, {
        construct(target, args, newTarget) {
          const instance = Reflect.construct(target, args, newTarget);
          window.__spk.fc = instance;
          try {
            const conf = args && args[0] ? args[0] : {};
            scan(conf, 'config');
          } catch (e) { window.__spk.errors.push(String(e)); }
          for (const m of ['getConfig', 'getToken', 'getSessionToken']) {
            if (typeof instance[m] === 'function') {
              const orig = instance[m].bind(instance);
              instance[m] = function () {
                const r = orig.apply(null, arguments);
                try { scan(r, 'instance.' + m); } catch (e) {}
                return r;
              };
            }
          }
          return instance;
        },
        apply(target, thisArg, args) {
          const r = Reflect.apply(target, thisArg, args);
          try { scan(r, 'call'); } catch (e) {}
          return r;
        },
      });
    },
  });
  // 3. ArkoseEnforcement — альтернативное имя точки входа.
  const hookEnforcement = (name) => {
    let real = window[name];
    if (!real) return;
    Object.defineProperty(window, name, {
      configurable: true,
      get() { return real; },
      set(fn) {
        if (typeof fn !== 'function') { real = fn; return; }
        real = new Proxy(fn, {
          construct(target, args, newTarget) {
            const inst = Reflect.construct(target, args, newTarget);
            window.__spk[name] = inst;
            try { scan(args[0], name); } catch (e) {}
            return inst;
          },
        });
      },
    });
  };
  hookEnforcement('ArkoseEnforcement');
})();
"""

COLLECT_JS = r"""
(() => {
  const s = window.__spk || { tokens: [], events: [], errors: [] };
  const out = { tokens: s.tokens.slice(-5), errors: s.errors.slice(-5), hasFc: !!s.fc };
  try {
    if (s.fc && typeof s.fc.getConfig === 'function') {
      out.config = s.fc.getConfig();
    }
  } catch (e) { out.configError = String(e); }
  return out;
})()
"""


class PlaywrightProvider:
    name = "playwright"

    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self._pw: Any = None
        self._browser: Any = None
        self._lock = asyncio.Lock()

    async def _ensure_browser(self) -> Any:
        if self._browser is not None:
            return self._browser
        async with self._lock:
            if self._browser is not None:
                return self._browser
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:  # среда без браузерного слоя
                raise ProviderUnavailable(
                    "playwright не установлен: pip install playwright && playwright install chromium"
                ) from exc
            try:
                self._pw = await async_playwright().start()
                launcher = getattr(self._pw, self.s.playwright_browser, None)
                if launcher is None:
                    raise ProviderUnavailable(
                        f"неизвестный браузер {self.s.playwright_browser!r}"
                    )
                self._browser = await launcher.launch(headless=self.s.playwright_headless)
            except ProviderUnavailable:
                raise
            except Exception as exc:
                raise ProviderUnavailable(
                    f"браузер не запускается ({type(exc).__name__}: {exc}). "
                    "Выполнить: playwright install chromium"
                ) from exc
        return self._browser

    async def solve(self, prompt: ChallengePrompt, profile: Profile) -> str:
        started = time.perf_counter()
        transferred = {"bytes": 0, "requests": 0}
        result_label = "failed"
        browser = await self._ensure_browser()
        context = None
        try:
            context = await browser.new_context(
                user_agent=profile.user_agent,
                locale=profile.accept_language.split(",")[0].split(";")[0] or "en-US",
                timezone_id="Europe/Moscow",
                proxy={"server": profile.proxy_url} if profile.proxy_url else None,
                viewport={"width": 1366, "height": 768},
                device_scale_factor=1,
                is_mobile=False,
                has_touch=False,
            )
            if profile.cookies:
                await context.add_cookies(
                    [
                        {"name": k, "value": v, "domain": ".roblox.com", "path": "/"}
                        for k, v in profile.cookies.items()
                    ]
                )
            await context.add_init_script(INIT_SCRIPT)

            network_tokens: list[str] = []

            async def on_response(resp: Any) -> None:
                url = resp.url
                if not any(marker in url for marker in TOKEN_URL_MARKERS):
                    return
                try:
                    if "json" not in (resp.headers.get("content-type") or ""):
                        return
                    payload = await resp.json()
                except Exception:
                    return
                for found in _walk_tokens(payload):
                    network_tokens.append(found)

            context.on("response", on_response)

            page = await context.new_page()
            page.set_default_timeout(self.s.challenge_timeout * 1000)

            # Учёт трафика: браузерный поток стоит мегабайты, и это должно
            # быть видно в метриках, а не выясняться по счёту провайдера.
            try:
                cdp = await context.new_cdp_session(page)
                await cdp.send("Network.enable")

                def _on_finished(params: Any) -> None:
                    transferred["bytes"] += params.get("encodedDataLength") or 0
                    transferred["requests"] += 1

                cdp.on("Network.loadingFinished", _on_finished)
            except Exception as exc:  # CDP может быть недоступен — не критично
                log(logr, 30, "учёт трафика недоступен", error=type(exc).__name__)

            if self.s.browser_block_assets:
                async def _block(route: Any, request: Any) -> None:
                    if request.resource_type in ("image", "media", "font"):
                        await route.abort()
                    else:
                        await route.continue_()

                await context.route("**/*", _block)

            await page.goto(
                f"{self.s.origin}/login",
                wait_until="domcontentloaded",
                timeout=self.s.challenge_timeout * 1000,
            )

            await self._drive(page, prompt)

            # Проверка PerimeterX «нажмите и удерживайте»: вход завершается
            # прямо в странице, токена для повторного обмена нет.
            if await self._solve_px(page, profile):
                raise SessionEstablished(
                    "проверка пройдена в браузере, сессионные cookie перенесены в профиль"
                )

            deadline = time.monotonic() + self.s.challenge_timeout
            last_state: dict[str, Any] = {}
            while time.monotonic() < deadline:
                state = await page.evaluate(COLLECT_JS)
                last_state = state
                for entry in state.get("tokens") or []:
                    token = (entry or {}).get("token")
                    if _plausible(token):
                        await self._remember(profile, page)
                        log(logr, 20, "токен получен из страницы",
                            strategy=entry.get("src"), solve_s=round(time.perf_counter() - started, 2))
                        return str(token)
                cfg = state.get("config") or {}
                for key in TOKEN_KEYS:
                    value = cfg.get(key) if isinstance(cfg, dict) else None
                    if _plausible(value):
                        await self._remember(profile, page)
                        log(logr, 20, "токен получен из конфигурации",
                            key=key, solve_s=round(time.perf_counter() - started, 2))
                        return str(value)
                for token in reversed(network_tokens):
                    if _plausible(token):
                        await self._remember(profile, page)
                        log(logr, 20, "токен получен из сети",
                            solve_s=round(time.perf_counter() - started, 2))
                        return token
                # После прохождения платформа сама уходит на /home.
                if "/home" in page.url or "/discover" in page.url:
                    await self._remember(profile, page)
                    raise ChallengeError(
                        "проверка пройдена в браузере, но токен не перехвачен: "
                        "сессия установлена в контексте, повторить обмен не требуется"
                    )
                await asyncio.sleep(0.5)

            raise ChallengeTimeout(
                "токен не получен за "
                f"{self.s.challenge_timeout:.0f}с; последнее состояние: "
                f"{_short(last_state)}; сетевых кандидатов: {len(network_tokens)}"
            )
        finally:
            if transferred["bytes"]:
                metrics.browser_bytes_total.inc(transferred["bytes"])
                log(logr, 20, "трафик браузерного прохождения",
                    mib=round(transferred["bytes"] / 1024 / 1024, 2),
                    requests=transferred["requests"],
                    solve_s=round(time.perf_counter() - started, 1))
            if context is not None:
                with contextlib.suppress(Exception):
                    await context.close()

    async def _solve_px(self, page: Any, profile: Profile) -> bool:
        """Удержание кнопки PerimeterX. True — сессия получена.

        Кнопка рисуется внутри диалога проверки, её текст не всегда доступен
        отдельным узлом, поэтому точка берётся от бокса самого диалога.
        """
        box: dict[str, float] | None = None
        for _ in range(15):
            info = await page.evaluate(
                """() => {
                  const dialogs = [...document.querySelectorAll('[role=dialog], [id^=radix]')]
                    .map(el => { const b = el.getBoundingClientRect();
                                 return {x: b.x, y: b.y, w: b.width, h: b.height, t: (el.innerText||'')}; })
                    .filter(d => d.w > 200 && d.h > 80);
                  if (!dialogs.length) return null;
                  dialogs.sort((a, b) => a.w * a.h - b.w * b.h);
                  const d = dialogs[0];
                  return {x: d.x, y: d.y, w: d.w, h: d.h,
                          hold: /hold|удержива/i.test(d.t)};
                }"""
            )
            if info and info.get("hold"):
                box = info
                break
            await asyncio.sleep(1.0)
        if box is None:
            log(logr, 20, "узла удержания нет")
            return False

        cx = box["x"] + box["w"] / 2
        cy = box["y"] + box["h"] * 0.58
        log(logr, 20, "удержание кнопки проверки", point=(round(cx), round(cy)),
            box=(round(box["w"]), round(box["h"])))
        metrics.browser_sessions_total.labels(result="px_hold").inc()

        await page.mouse.move(cx, cy, steps=10)
        await page.wait_for_timeout(180)
        await page.mouse.down()
        started = time.monotonic()
        while time.monotonic() - started < self.s.px_hold_seconds:
            await page.wait_for_timeout(120)
            await page.mouse.move(cx + random.uniform(-2.5, 2.5), cy + random.uniform(-2.0, 2.0))
        await page.mouse.up()
        held = round(time.monotonic() - started, 1)
        log(logr, 20, "удержание отпущено", held_s=held)

        deadline = time.monotonic() + self.s.px_session_wait
        while time.monotonic() < deadline:
            await page.wait_for_timeout(1000)
            cookies = await page.context.cookies()
            session = [c for c in cookies if c.get("name") == ".ROBLOSECURITY" and c.get("value")]
            if session:
                await self._remember(profile, page)
                log(logr, 20, "сессия получена в браузере", len_session=len(session[0]["value"]))
                return True
            if "/home" in page.url or "/discover" in page.url:
                await self._remember(profile, page)
                log(logr, 20, "редирект в кабинет после проверки")
                return True
        log(logr, 30, "сессии в браузере нет после удержания")
        return False

    async def _drive(self, page: Any, prompt: ChallengePrompt) -> None:
        """Довести страницу до показа узла проверки."""
        if prompt.captcha_url:
            await page.goto(prompt.captcha_url, wait_until="domcontentloaded")
            return
        # Узел уже может быть в модалке после загрузки /login; если нет —
        # подталкиваем форму, чтобы платформа сама его показала.
        try:
            await page.wait_for_selector(
                "#funcaptcha-modal-body", timeout=8_000, state="attached"
            )
            log(logr, 20, "модалка узла проверки найдена")
            return
        except Exception:
            log(logr, 20, "модалка не появилась сразу, продолжаем ожидание")
        site_key = prompt.site_key
        if site_key:
            script = f"{self.s.origin}/../arkose"
            log(logr, 10, "ожидание узла без принудительной подстановки", hint=script)

    async def _remember(self, profile: Profile, page: Any) -> None:
        """Забрать cookie из браузера в банку профиля — контекст тот же."""
        with contextlib.suppress(Exception):
            cookies = await page.context.cookies()
            for c in cookies:
                name = c.get("name")
                value = c.get("value")
                if name and value:
                    profile.cookies[name] = value

    async def aclose(self) -> None:
        if self._browser is not None:
            with contextlib.suppress(Exception):
                await self._browser.close()
            self._browser = None
        if self._pw is not None:
            with contextlib.suppress(Exception):
                await self._pw.stop()
            self._pw = None


def _walk_tokens(payload: Any, depth: int = 0) -> list[str]:
    out: list[str] = []
    if depth > 6:
        return out
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in TOKEN_KEYS and isinstance(value, str) and _plausible(value):
                out.append(value)
            elif isinstance(value, (dict, list)):
                out.extend(_walk_tokens(value, depth + 1))
    elif isinstance(payload, list):
        for item in payload:
            out.extend(_walk_tokens(item, depth + 1))
    return out


def _plausible(token: Any) -> bool:
    return isinstance(token, str) and len(token) >= 40 and " " not in token


def _short(state: dict[str, Any]) -> str:
    keys = ("hasFc", "tokens", "errors")
    return str({k: state.get(k) for k in keys})[:400]
