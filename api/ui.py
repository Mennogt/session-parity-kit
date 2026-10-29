"""Веб-панель: удобный интерфейс над сервисом.

Отдаёт один самодостаточный HTML (без сборки и зависимостей) и несколько
read-only ручек для данных, которые лежат в `var/`. Вход выполняется через
существующий `/api/v1/login`, поэтому панель не дублирует логику.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import httpx
import psutil
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter(tags=["ui"])

ACCOUNTS_FILE = Path("var/accounts.json")
SESSIONS_FILE = Path("var/sessions.json")
PROXIES_FILE = Path("var/proxies.txt")
PROFILES_FILE = Path("var/profiles.json")
VERIFY_URL = "https://users.roblox.com/v1/users/authenticated"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index() -> str:
    return PAGE


@router.get("/ui/accounts", include_in_schema=False)
async def accounts() -> JSONResponse:
    if not ACCOUNTS_FILE.exists():
        return JSONResponse({"items": [], "note": "нет var/accounts.json"})
    try:
        rows = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        return JSONResponse({"items": [], "note": f"файл не разобран: {exc}"})
    items = [
        {"login": str(row.get("login", "")), "has_password": bool(row.get("password"))}
        for row in rows
        if row.get("login")
    ]
    return JSONResponse({"items": items, "count": len(items)})


@router.get("/ui/sessions", include_in_schema=False)
async def sessions() -> JSONResponse:
    if not SESSIONS_FILE.exists():
        return JSONResponse({"items": [], "note": "банк пуст"})
    try:
        bank = json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        return JSONResponse({"items": [], "note": f"файл не разобран: {exc}"})
    items = []
    for value in bank.values():
        items.append({
            "login": value.get("login"),
            "length": int(value.get("len") or len(str(value.get("cookie", "")))),
            "ok": bool(value.get("ok")),
            "verified_at": value.get("verified_at"),
            "identity": value.get("identity"),
        })
    items.sort(key=lambda row: str(row["login"]).lower())
    return JSONResponse({"items": items, "count": len(items)})


@router.post("/ui/sessions/verify", include_in_schema=False)
async def verify_sessions() -> JSONResponse:
    """Проверить все сессии банка контрольным запросом."""
    if not SESSIONS_FILE.exists():
        return JSONResponse({"checked": 0, "alive": 0})
    bank = json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
    checked = alive = 0
    details = []
    async with httpx.AsyncClient(timeout=25) as client:
        for key, value in bank.items():
            cookie = value.get("cookie", "")
            if not cookie:
                continue
            checked += 1
            try:
                resp = await client.get(
                    VERIFY_URL,
                    headers={"User-Agent": UA, "Accept": "application/json"},
                    cookies={".ROBLOSECURITY": cookie},
                )
                ok = resp.status_code == 200
                name = resp.json().get("name") if ok else None
            except Exception:
                ok, name = False, None
            alive += int(ok)
            value["ok"] = ok
            value["verified_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            if name:
                value["identity"] = name
            details.append({"login": value.get("login"), "ok": ok, "identity": name})
    SESSIONS_FILE.write_text(json.dumps(bank, ensure_ascii=False, indent=2), encoding="utf-8")
    return JSONResponse({"checked": checked, "alive": alive, "items": details})


@router.get("/ui/sessions/cookie", include_in_schema=False)
async def session_cookie(login: str) -> JSONResponse:
    """Значение cookie для копирования вручную (локальная панель)."""
    if not SESSIONS_FILE.exists():
        return JSONResponse({"error": "банк пуст"}, status_code=404)
    bank = json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
    entry = bank.get(login.lower())
    if not entry or not entry.get("cookie"):
        return JSONResponse({"error": "нет такой сессии"}, status_code=404)
    return JSONResponse({"login": entry.get("login"), "cookie": entry["cookie"]})



ACCOUNTS_SEPARATORS = (":", ";", "\t")


def _parse_accounts(text: str) -> list[dict[str, str]]:
    """Строки вида `логин:пароль`; пароль может содержать двоеточие."""
    rows: list[dict[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        for separator in ACCOUNTS_SEPARATORS:
            if separator in line:
                login, _, password = line.partition(separator)
                break
        else:
            parts = line.split()
            if len(parts) < 2:
                continue
            login, password = parts[0], " ".join(parts[1:])
        login, password = login.strip(), password.strip()
        if login and password:
            rows.append({"login": login, "password": password})
    return rows


def _parse_proxies(text: str) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "://" not in line:
            line = "http://" + line
        if line not in seen:
            seen.add(line)
            rows.append(line)
    return rows


@router.post("/ui/accounts/import", include_in_schema=False)
async def import_accounts(request: Request) -> JSONResponse:
    body = await request.json()
    parsed = _parse_accounts(str(body.get("text") or ""))
    if not parsed:
        return JSONResponse({"added": 0, "updated": 0, "total": 0,
                             "note": "не разобрано ни одной строки"})
    existing: list[dict] = []
    if ACCOUNTS_FILE.exists():
        try:
            existing = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            existing = []
    index = {str(row.get("login", "")).lower(): row for row in existing}
    added = updated = 0
    for row in parsed:
        key = row["login"].lower()
        if key in index:
            index[key]["password"] = row["password"]
            updated += 1
        else:
            existing.append(row)
            index[key] = row
            added += 1
    ACCOUNTS_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    return JSONResponse({"added": added, "updated": updated, "total": len(existing)})


@router.post("/ui/proxies/import", include_in_schema=False)
async def import_proxies(request: Request) -> JSONResponse:
    body = await request.json()
    rows = _parse_proxies(str(body.get("text") or ""))
    if not rows:
        return JSONResponse({"added": 0, "total": 0,
                             "note": "не разобрано ни одной строки"})
    existing: list[str] = []
    if PROXIES_FILE.exists():
        existing = _parse_proxies(PROXIES_FILE.read_text(encoding="utf-8"))
    merged = _parse_proxies("\n".join(existing + rows))
    PROXIES_FILE.write_text("\n".join(merged) + "\n", encoding="utf-8")
    return JSONResponse({"added": len(merged) - len(existing), "total": len(merged)})


@router.post("/ui/proxies/apply", include_in_schema=False)
async def apply_proxies() -> JSONResponse:
    """Собрать пул профилей из var/proxies.txt. Нужен перезапуск воркеров."""
    if not PROXIES_FILE.exists():
        return JSONResponse({"profiles": 0, "note": "нет var/proxies.txt"})
    rows = _parse_proxies(PROXIES_FILE.read_text(encoding="utf-8"))
    if not rows:
        return JSONResponse({"profiles": 0, "note": "список пуст"})
    profiles = [{"id": f"px-{i:03d}", "proxy_url": url} for i, url in enumerate(rows, 1)]
    PROFILES_FILE.write_text(json.dumps(profiles, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    return JSONResponse({"profiles": len(profiles),
                         "note": "перезапустите воркеры, чтобы пул обновился"})


@router.post("/ui/batch", include_in_schema=False)
async def batch(request: Request) -> JSONResponse:
    """Поставить в очередь все аккаунты (или перечисленные)."""
    body = await request.json()
    wanted = body.get("logins") or []
    limit = int(body.get("limit") or 500)
    container = getattr(request.app.state, "container", None)
    if container is None or not ACCOUNTS_FILE.exists():
        return JSONResponse({"queued": 0, "note": "нет контейнера или файла аккаунтов"})
    accounts = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    wanted_set = {str(item).lower() for item in wanted}
    selected = [row for row in accounts
                if not wanted_set or str(row.get("login", "")).lower() in wanted_set]
    queued = 0
    for row in selected[:limit]:
        login, password = row.get("login"), row.get("password")
        if not login or not password:
            continue
        job_id, state = await asyncio.to_thread(
            container.store.create_job, login, password, None)
        if str(state.get("state")) != "DONE":
            await container.queue.put(job_id)
        queued += 1
    return JSONResponse({"queued": queued, "selected": len(selected[:limit])})


@router.get("/ui/system", include_in_schema=False)
async def system(request: Request) -> JSONResponse:
    """Живая телеметрия: CPU, память, потоки, входы в минуту, воркеры."""
    container = getattr(request.app.state, "container", None)
    if container is None:
        return JSONResponse({"ready": False})

    rss = threads = 0
    cpu = 0.0
    processes = 0
    for proc in psutil.process_iter(["cmdline", "memory_info", "num_threads"]):
        cmd = " ".join(proc.info["cmdline"] or [])
        if "uvicorn" in cmd or "worker.main" in cmd:
            processes += 1
            rss += proc.info["memory_info"].rss
            threads += proc.info["num_threads"]
            try:
                cpu += proc.cpu_percent(interval=None)
            except Exception:
                pass

    memory = psutil.virtual_memory()
    target = await asyncio.to_thread(container.store.meta_get, "worker_target")
    success = await asyncio.to_thread(container.store.recent_successes, 60.0)
    workers = await asyncio.to_thread(container.store.active_workers, 30.0)
    depth = await container.queue.depth()
    cpu_count = os.cpu_count() or 1
    return JSONResponse({
        "ready": True,
        "cpu_percent": round(psutil.cpu_percent(interval=None) / cpu_count, 1),
        "cpu_total_percent": round(psutil.cpu_percent(interval=None), 1),
        "cpu_count": cpu_count,
        "ram_used_mb": round(memory.used / 1048576),
        "ram_total_mb": round(memory.total / 1048576),
        "service_rss_mb": round(rss / 1048576, 1),
        "service_threads": threads,
        "processes": processes,
        "logins_per_min": success,
        "workers_active": workers,
        "workers_target": int(target) if target else None,
        "queue_depth": depth,
    })


@router.post("/ui/workers", include_in_schema=False)
async def set_workers(request: Request) -> JSONResponse:
    """Задать желаемое число воркеров (пул подстроится за ~3 секунды)."""
    body = await request.json()
    try:
        count = max(1, min(64, int(body.get("count") or 1)))
    except (TypeError, ValueError):
        return JSONResponse({"error": "нужно целое число"}, status_code=400)
    container = getattr(request.app.state, "container", None)
    if container is None:
        return JSONResponse({"error": "сервис не готов"}, status_code=503)
    await asyncio.to_thread(container.store.meta_set, "worker_target", str(count))
    return JSONResponse({"workers_target": count, "note": "применится в течение 3 секунд"})


@router.get("/ui/overview", include_in_schema=False)
async def overview(request: Request) -> JSONResponse:
    """Сводка для панели: очередь, воркеры, статистика, банк."""
    container = getattr(request.app.state, "container", None)
    if container is None:
        return JSONResponse({"ready": False})
    store = container.store
    stats = await asyncio.to_thread(store.stats)
    workers = await asyncio.to_thread(store.active_workers, 30.0)
    depth = await container.queue.depth()
    profiles = await asyncio.to_thread(store.profile_stats)
    return JSONResponse({
        "ready": True,
        "queue_depth": depth,
        "workers": workers,
        "stats": stats,
        "profiles": profiles,
    })


PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Session Parity Kit — панель</title>
<style>
  :root {
    --bg: #0d1117; --panel: #161b22; --panel-2: #1c2330; --stroke: #2a3341;
    --text: #e6edf3; --muted: #8b949e; --accent: #2f81f7; --good: #3fb950;
    --warn: #d29922; --bad: #f85149; --radius: 14px;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background:
      radial-gradient(1200px 600px at 10% -10%, #1b2a4a 0%, transparent 55%),
      radial-gradient(900px 500px at 100% 0%, #241a3d 0%, transparent 50%),
      var(--bg);
    color: var(--text); min-height: 100vh;
    font: 15px/1.55 "Segoe UI", -apple-system, Roboto, Helvetica, Arial, sans-serif;
  }
  .wrap { max-width: 1180px; margin: 0 auto; padding: 32px 24px 64px; }
  header { display: flex; align-items: center; gap: 14px; margin-bottom: 6px; }
  .logo {
    width: 40px; height: 40px; border-radius: 12px; flex: none;
    background: linear-gradient(135deg, #2f81f7, #a371f7);
    box-shadow: 0 8px 24px rgba(47,129,247,.35);
    display: grid; place-items: center; font-weight: 700; font-size: 18px;
  }
  h1 { font-size: 21px; margin: 0; letter-spacing: .2px; }
  .sub { color: var(--muted); font-size: 13px; margin-top: 2px; }
  .grid { display: grid; gap: 16px; grid-template-columns: repeat(4, 1fr); margin: 26px 0; }
  .card {
    background: linear-gradient(180deg, var(--panel), var(--panel-2));
    border: 1px solid var(--stroke); border-radius: var(--radius); padding: 16px 18px;
  }
  .card .label { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .6px; }
  .card .value { font-size: 26px; font-weight: 650; margin-top: 6px; font-variant-numeric: tabular-nums; }
  .card .value.good { color: var(--good); } .card .value.bad { color: var(--bad); }
  .card .value.warn { color: var(--warn); } .card .value.acc { color: var(--accent); }
  .panels { display: grid; gap: 16px; grid-template-columns: 1.15fr .85fr; }
  .panel {
    background: linear-gradient(180deg, var(--panel), var(--panel-2));
    border: 1px solid var(--stroke); border-radius: var(--radius); padding: 18px;
  }
  .panel h2 { font-size: 15px; margin: 0 0 14px; letter-spacing: .3px; }
  label { display: block; color: var(--muted); font-size: 12px; margin: 10px 0 6px; }
  input, select {
    width: 100%; padding: 11px 12px; border-radius: 10px; border: 1px solid var(--stroke);
    background: #0f141b; color: var(--text); font: inherit; outline: none;
  }
  input:focus, select:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(47,129,247,.18); }
  .row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  button {
    margin-top: 16px; width: 100%; padding: 12px 16px; border: 0; border-radius: 10px;
    background: linear-gradient(135deg, #2f81f7, #6f7bf7); color: #fff; font: inherit;
    font-weight: 600; cursor: pointer; transition: transform .08s ease, filter .15s ease;
  }
  button:hover { filter: brightness(1.08); } button:active { transform: translateY(1px); }
  button.ghost { background: #21262d; border: 1px solid var(--stroke); font-weight: 500; }
  table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
  th { text-align: left; color: var(--muted); font-weight: 500; font-size: 12px; padding: 8px 6px; }
  td { padding: 9px 6px; border-top: 1px solid var(--stroke); }
  .pill { padding: 3px 9px; border-radius: 999px; font-size: 11.5px; font-weight: 600; }
  .pill.ok { background: rgba(63,185,80,.15); color: var(--good); }
  .pill.bad { background: rgba(248,81,73,.15); color: var(--bad); }
  .pill.wait { background: rgba(210,153,34,.15); color: var(--warn); }
  .pill.info { background: rgba(47,129,247,.15); color: var(--accent); }
  .muted { color: var(--muted); }
  .mono { font-family: Consolas, Menlo, monospace; font-size: 12.5px; }
  .toast {
    position: fixed; right: 24px; bottom: 24px; padding: 13px 18px; border-radius: 12px;
    background: #1f6feb; color: #fff; box-shadow: 0 12px 30px rgba(0,0,0,.45);
    opacity: 0; transform: translateY(10px); transition: all .25s ease; pointer-events: none;
  }
  .toast.show { opacity: 1; transform: translateY(0); }
  .toast.err { background: var(--bad); }
  .spinner {
    width: 14px; height: 14px; border-radius: 50%; display: inline-block; vertical-align: -2px;
    border: 2px solid rgba(255,255,255,.35); border-top-color: #fff; animation: spin .8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  button.copy {
    width: auto; margin: 0; padding: 4px 10px; font-size: 11.5px; font-weight: 500;
    background: #21262d; border: 1px solid var(--stroke); color: var(--muted);
  }
  button.copy:hover { color: var(--text); border-color: var(--accent); }
  .fileline input[type=file]::file-selector-button {
    background: #21262d; color: var(--text); border: 1px solid var(--stroke);
    border-radius: 8px; padding: 7px 12px; margin-right: 10px; cursor: pointer;
    font: inherit; font-size: 12px;
  }
  .fileline input[type=file]::file-selector-button:hover { border-color: var(--accent); }
  textarea {
    width: 100%; padding: 11px 12px; border-radius: 10px; border: 1px solid var(--stroke);
    background: #0f141b; color: var(--text); font: 13px/1.5 Consolas, Menlo, monospace;
    resize: vertical; outline: none;
  }
  textarea:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(47,129,247,.18); }
  .fileline { display: flex; gap: 10px; align-items: center; margin-top: 10px; }
  .fileline input[type=file] { color: var(--muted); font-size: 12px; max-width: 55%; }
  button.small { margin: 0; padding: 8px 12px; width: auto; font-size: 12.5px; }
  .route { margin-top: 18px; display: grid; gap: 10px; }
  .route .step { display: flex; align-items: center; gap: 10px; font-size: 13px; }
  .route .step b { min-width: 76px; font-weight: 600; }
  .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--good); flex: none;
         box-shadow: 0 0 8px rgba(63,185,80,.6); }
  footer { margin-top: 28px; color: var(--muted); font-size: 12px; display: flex; justify-content: space-between; }
  @media (max-width: 900px) { .grid { grid-template-columns: repeat(2, 1fr); } .panels { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="logo">SP</div>
    <div>
      <h1>Session Parity Kit</h1>
      <div class="sub">вход по логину и паролю · HTTP-путь без капчи · банк сессий</div>
    </div>
  </header>

  <section class="grid">
    <div class="card"><div class="label">Воркеры</div><div class="value acc" id="workers">—</div></div>
    <div class="card"><div class="label">В очереди</div><div class="value" id="depth">—</div></div>
    <div class="card"><div class="label">Успешных входов</div><div class="value good" id="success">—</div></div>
    <div class="card"><div class="label">Сессий в банке</div><div class="value" id="sessions">—</div></div>
  </section>

  <section class="panels">
    <div class="panel">
      <h2>Вход в аккаунт</h2>
      <div class="row">
        <div>
          <label>Логин</label>
          <input id="login" list="accounts" placeholder="начните вводить" autocomplete="off">
          <datalist id="accounts"></datalist>
        </div>
        <div>
          <label>Пароль</label>
          <input id="password" type="password" placeholder="пароль аккаунта">
        </div>
      </div>
      <label>Ждать результат</label>
      <select id="wait">
        <option value="0">нет, просто поставить в очередь</option>
        <option value="30" selected>до 30 секунд</option>
        <option value="60">до 60 секунд</option>
      </select>
      <button id="submit">Войти</button>
      <div style="margin-top:14px" class="muted" id="submitNote">Живой путь: подпись → proof-of-work (1.2 с) → токен второй ступени → сессия.</div>

      <div class="route">
        <div class="step"><span class="dot"></span><b>Подпись</b><span class="muted">secureAuthenticationIntent</span></div>
        <div class="step"><span class="dot"></span><b>Задача</b><span class="muted">A^(2^T) mod N · 1.2 с</span></div>
        <div class="step"><span class="dot"></span><b>Токен</b><span class="muted">/v2/captcha → redemption_token</span></div>
        <div class="step"><span class="dot"></span><b>Сессия</b><span class="muted">подтверждение контрольным запросом</span></div>
      </div>
    </div>

    <div class="panel">
      <h2>Банк сессий</h2>
      <table>
        <thead><tr><th>Аккаунт</th><th>Состояние</th><th>Проверено</th><th></th></tr></thead>
        <tbody id="bank"><tr><td colspan="3" class="muted">загрузка…</td></tr></tbody>
      </table>
      <button class="ghost" id="verify">Проверить все сессии</button>
    </div>
  </section>

  <section class="panel" style="margin-top:16px">
    <h2>Телеметрия</h2>
    <div class="grid" style="margin:0 0 14px">
      <div class="card"><div class="label">CPU системы</div><div class="value acc" id="tCpu">—</div></div>
      <div class="card"><div class="label">Память</div><div class="value" id="tRam">—</div></div>
      <div class="card"><div class="label">Сервис: RSS / потоки</div><div class="value" id="tRss">—</div></div>
      <div class="card"><div class="label">Входов в минуту</div><div class="value good" id="tKpm">—</div></div>
    </div>
    <div class="row">
      <div>
        <label>Воркеры — применится за ~3 секунды</label>
        <div class="fileline" style="margin-top:0">
          <input type="number" id="workersInput" min="1" max="64" value="2" style="max-width:120px">
          <button class="ghost small" id="applyWorkers">Применить</button>
          <span class="muted" id="workersNow">—</span>
        </div>
        <div class="muted" style="margin-top:8px;font-size:12.5px">
          воркер — это параллельное задание внутри процесса, а не поток
        </div>
      </div>
      <div>
        <label>Нагрузка: CPU (синий) и входы/мин (зелёный)</label>
        <canvas id="spark" height="74" style="width:100%"></canvas>
      </div>
    </div>
  </section>

  <section class="panel" style="margin-top:16px">
    <h2>Массовый ввоз</h2>
    <div class="row">
      <div>
        <label>Аккаунты — по строке <span class="mono">логин:пароль</span></label>
        <textarea id="accText" rows="6" placeholder="user1:pass1&#10;user2:pass2"></textarea>
        <div class="fileline"><input type="file" id="accFile" accept=".txt,.csv">
          <button class="ghost small" id="importAcc">Добавить аккаунты</button></div>
      </div>
      <div>
        <label>Прокси — по строке</label>
        <textarea id="pxText" rows="6" placeholder="http://user:pass@host:port"></textarea>
        <div class="fileline"><input type="file" id="pxFile" accept=".txt">
          <button class="ghost small" id="importPx">Добавить прокси</button></div>
      </div>
    </div>
    <div class="row" style="margin-top:12px">
      <button id="batchAll">Запустить все аккаунты</button>
      <button class="ghost" id="applyPx">Собрать пул прокси</button>
    </div>
  </section>

  <section class="panel" style="margin-top:16px">
    <h2>Последние задания</h2>
    <table>
      <thead><tr><th>Задание</th><th>Логин</th><th>Состояние</th><th>Статус</th><th>Шаг</th><th>Время</th></tr></thead>
      <tbody id="jobs"><tr><td colspan="6" class="muted">загрузка…</td></tr></tbody>
    </table>
  </section>

  <footer>
    <span>локальный сервис · данные остаются на машине</span>
    <span class="mono" id="stamp"></span>
  </footer>
</div>
<div class="toast" id="toast"></div>

<script>
const $ = (id) => document.getElementById(id);
const toast = (text, err) => {
  const el = $("toast");
  el.textContent = text;
  el.className = "toast show" + (err ? " err" : "");
  clearTimeout(el._t);
  el._t = setTimeout(() => (el.className = "toast"), 3200);
};
const fmt = (v) => (v === null || v === undefined ? "—" : v);

async function api(path, options) {
  const resp = await fetch(path, options);
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);
  return resp.json();
}

async function loadOverview() {
  try {
    const data = await api("/ui/overview");
    if (!data.ready) return;
    $("workers").textContent = fmt(data.workers);
    $("depth").textContent = fmt(data.queue_depth);
    $("success").textContent = fmt((data.stats || {}).SUCCESS ?? 0);
    const bank = await api("/ui/sessions");
    $("sessions").textContent = fmt(bank.count ?? 0);
  } catch (e) { /* сервис ещё поднимается */ }
}

async function loadAccounts() {
  try {
    const data = await api("/ui/accounts");
    const list = $("accounts");
    list.innerHTML = "";
    (data.items || []).forEach((item) => {
      const opt = document.createElement("option");
      opt.value = item.login;
      list.appendChild(opt);
    });
  } catch (e) { /* нет файла аккаунтов */ }
}

async function loadBank() {
  try {
    const data = await api("/ui/sessions");
    const body = $("bank");
    if (!data.items || !data.items.length) {
      body.innerHTML = '<tr><td colspan="4" class="muted">банк пуст</td></tr>';
      return;
    }
    body.innerHTML = data.items.map((row) => `
      <tr>
        <td class="mono">${row.login}</td>
        <td><span class="pill ${row.ok ? "ok" : "bad"}">${row.ok ? "живая" : "мёртвая"}</span></td>
        <td class="muted mono">${(row.verified_at || "").slice(0, 16) || "—"}</td>
        <td style="text-align:right"><button class="copy" data-login="${row.login}">cookie</button></td>
      </tr>`).join("");
    $("sessions").textContent = data.count;
  } catch (e) { /* ignore */ }
}

async function loadJobs() {
  try {
    const data = await api("/api/v1/jobs?limit=12");
    const rows = data.items || data.jobs || data || [];
    const body = $("jobs");
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="6" class="muted">пока пусто</td></tr>';
      return;
    }
    body.innerHTML = rows.map((job) => {
      const state = job.state || "—";
      const cls = state === "DONE" ? "ok" : state === "RUNNING" ? "info" : "wait";
      const status = job.status || "—";
      const statusCls = status === "SUCCESS" ? "ok" : status === "—" ? "wait" : "bad";
      return `<tr>
        <td class="mono">${(job.job_id || "").slice(0, 10)}</td>
        <td class="mono">${job.login || "—"}</td>
        <td><span class="pill ${cls}">${state}</span></td>
        <td><span class="pill ${statusCls}">${status}</span></td>
        <td class="muted">${job.step || "—"}</td>
        <td class="muted mono">${job.elapsed_ms ? job.elapsed_ms + " мс" : "—"}</td>
      </tr>`;
    }).join("");
  } catch (e) { /* ignore */ }
}

$("submit").addEventListener("click", async () => {
  const login = $("login").value.trim();
  const password = $("password").value;
  if (!login || !password) return toast("нужны логин и пароль", true);
  const button = $("submit");
  button.disabled = true;
  button.innerHTML = '<span class="spinner"></span> входим…';
  try {
    const payload = { login, password, wait_seconds: Number($("wait").value) };
    const data = await api("/api/v1/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (data.status === "SUCCESS") toast("сессия получена");
    else if (data.state === "DONE") toast("ответ: " + (data.status || "готово"), data.status !== "SUCCESS");
    else toast("задание в очереди: " + data.job_id);
    await Promise.all([loadOverview(), loadJobs(), loadBank()]);
  } catch (e) {
    toast("ошибка: " + e.message.slice(0, 120), true);
  } finally {
    button.disabled = false;
    button.textContent = "Войти";
  }
});

$("verify").addEventListener("click", async () => {
  const button = $("verify");
  button.disabled = true;
  button.innerHTML = '<span class="spinner"></span> проверяем…';
  try {
    const data = await api("/ui/sessions/verify", { method: "POST" });
    toast(`проверено ${data.checked}, живых ${data.alive}`);
    await loadBank();
  } catch (e) {
    toast("ошибка проверки", true);
  } finally {
    button.disabled = false;
    button.textContent = "Проверить все сессии";
  }
});

document.addEventListener("click", async (event) => {
  const button = event.target.closest(".copy");
  if (!button) return;
  const login = button.dataset.login;
  button.disabled = true;
  try {
    const data = await api("/ui/sessions/cookie?login=" + encodeURIComponent(login));
    await navigator.clipboard.writeText(data.cookie);
    toast("cookie для " + login + " скопирован");
  } catch (error) {
    toast("не удалось скопировать", true);
  } finally {
    button.disabled = false;
  }
});

const readFile = (input, target) => {
  const file = input.files && input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => { $(target).value = String(reader.result || ""); };
  reader.readAsText(file);
};
$("accFile").addEventListener("change", () => readFile($("accFile"), "accText"));
$("pxFile").addEventListener("change", () => readFile($("pxFile"), "pxText"));

const post = (path, body) => api(path, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body || {}),
});

$("importAcc").addEventListener("click", async () => {
  try {
    const data = await post("/ui/accounts/import", { text: $("accText").value });
    toast(`аккаунтов: добавлено ${data.added}, обновлено ${data.updated}, всего ${data.total}`);
    $("accText").value = "";
    await loadAccounts();
  } catch (e) { toast("не удалось импортировать аккаунты", true); }
});

$("importPx").addEventListener("click", async () => {
  try {
    const data = await post("/ui/proxies/import", { text: $("pxText").value });
    toast(`прокси: добавлено ${data.added}, всего ${data.total}`);
    $("pxText").value = "";
  } catch (e) { toast("не удалось импортировать прокси", true); }
});

$("applyPx").addEventListener("click", async () => {
  try {
    const data = await post("/ui/proxies/apply", {});
    toast(`профилей собрано: ${data.profiles}. ${data.note || ""}`);
  } catch (e) { toast("не удалось собрать пул", true); }
});

$("batchAll").addEventListener("click", async () => {
  const button = $("batchAll");
  button.disabled = true;
  button.innerHTML = '<span class="spinner"></span> ставим в очередь…';
  try {
    const data = await post("/ui/batch", {});
    toast(`в очередь поставлено: ${data.queued}`);
    await Promise.all([loadOverview(), loadJobs()]);
  } catch (e) { toast("не удалось запустить пачку", true); }
  finally { button.disabled = false; button.textContent = "Запустить все аккаунты"; }
});

const sparkData = { cpu: [], kpm: [] };
function drawSpark() {
  const canvas = $("spark");
  const ctx = canvas.getContext("2d");
  const width = canvas.width = canvas.clientWidth * 2;
  const height = canvas.height = 148;
  ctx.clearRect(0, 0, width, height);
  const draw = (series, color, scale) => {
    if (series.length < 2) return;
    ctx.beginPath();
    ctx.lineWidth = 3;
    ctx.strokeStyle = color;
    series.forEach((value, index) => {
      const x = (index / (series.length - 1)) * width;
      const y = height - Math.min(1, value / scale) * (height - 6) - 3;
      if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  };
  draw(sparkData.cpu, "#2f81f7", 100);
  draw(sparkData.kpm, "#3fb950", Math.max(1, ...sparkData.kpm));
}

async function loadSystem() {
  try {
    const data = await api("/ui/system");
    if (!data.ready) return;
    $("tCpu").textContent = data.cpu_total_percent + " %";
    $("tRam").textContent = data.ram_used_mb + " / " + data.ram_total_mb + " ГБ·МБ";
    $("tRam").textContent = (data.ram_used_mb / 1024).toFixed(1) + " / " +
                            (data.ram_total_mb / 1024).toFixed(0) + " ГБ";
    $("tRss").textContent = data.service_rss_mb + " МБ / " + data.service_threads;
    $("tKpm").textContent = data.logins_per_min;
    $("workersNow").textContent = "сейчас активно: " + data.workers_active +
      (data.workers_target ? " · цель: " + data.workers_target : "");
    if (data.workers_target) $("workersInput").value = data.workers_target;
    sparkData.cpu.push(data.cpu_total_percent || 0);
    sparkData.kpm.push(data.logins_per_min || 0);
    if (sparkData.cpu.length > 60) { sparkData.cpu.shift(); sparkData.kpm.shift(); }
    drawSpark();
  } catch (error) { /* сервис перезапускается */ }
}

$("applyWorkers").addEventListener("click", async () => {
  const count = Number($("workersInput").value || 2);
  try {
    const data = await post("/ui/workers", { count });
    toast("воркеров задано: " + data.workers_target + " · " + (data.note || ""));
    await loadSystem();
  } catch (error) { toast("не удалось применить", true); }
});

const tick = () => {
  $("stamp").textContent = new Date().toLocaleTimeString("ru-RU");
};
tick(); setInterval(tick, 1000);
loadAccounts(); loadBank(); loadJobs(); loadOverview();
setInterval(() => { loadOverview(); loadJobs(); loadBank(); }, 5000);
</script>
</body>
</html>
"""
