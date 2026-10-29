"""Разовое дополнение панели: маршрут входа и ручка cookie."""

from __future__ import annotations

import pathlib

P = pathlib.Path("api/ui.py")
t = P.read_text(encoding="utf-8")

COOKIE_ROUTE = '''@router.get("/ui/sessions/cookie", include_in_schema=False)
async def session_cookie(login: str) -> JSONResponse:
    """Значение cookie для копирования вручную (локальная панель)."""
    if not SESSIONS_FILE.exists():
        return JSONResponse({"error": "банк пуст"}, status_code=404)
    bank = json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
    entry = bank.get(login.lower())
    if not entry or not entry.get("cookie"):
        return JSONResponse({"error": "нет такой сессии"}, status_code=404)
    return JSONResponse({"login": entry.get("login"), "cookie": entry["cookie"]})


@router.get("/ui/overview", include_in_schema=False)'''

if "/ui/sessions/cookie" not in t:
    t = t.replace('@router.get("/ui/overview", include_in_schema=False)', COOKIE_ROUTE, 1)

OLD = '''      <button id="submit">Войти</button>
      <div style="margin-top:12px" class="muted" id="submitNote">Живой путь: подпись → proof-of-work (1.2 с) → токен второй ступени → сессия.</div>'''
NEW = '''      <button id="submit">Войти</button>
      <div style="margin-top:14px" class="muted" id="submitNote">Живой путь: подпись → proof-of-work (1.2 с) → токен второй ступени → сессия.</div>

      <div class="route">
        <div class="step"><span class="dot"></span><b>Подпись</b><span class="muted">secureAuthenticationIntent</span></div>
        <div class="step"><span class="dot"></span><b>Задача</b><span class="muted">A^(2^T) mod N · 1.2 с</span></div>
        <div class="step"><span class="dot"></span><b>Токен</b><span class="muted">/v2/captcha → redemption_token</span></div>
        <div class="step"><span class="dot"></span><b>Сессия</b><span class="muted">подтверждение контрольным запросом</span></div>
      </div>'''
if "class=\"route\"" not in t:
    t = t.replace(OLD, NEW, 1)

CSS = '''  .route { margin-top: 18px; display: grid; gap: 10px; }
  .route .step { display: flex; align-items: center; gap: 10px; font-size: 13px; }
  .route .step b { min-width: 76px; font-weight: 600; }
  .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--good); flex: none;
         box-shadow: 0 0 8px rgba(63,185,80,.6); }
  footer {'''
if ".route {" not in t:
    t = t.replace("  footer {", CSS, 1)

P.write_text(t, encoding="utf-8")
print("маршрут:", ".route {" in t, "| ручка cookie:", "/ui/sessions/cookie" in t)
