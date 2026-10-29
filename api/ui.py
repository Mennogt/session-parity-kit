"""Веб-панель: удобный интерфейс над сервисом.

Отдаёт один самодостаточный HTML (без сборки и зависимостей) и несколько
read-only ручек для данных, которые лежат в `var/`. Вход выполняется через
существующий `/api/v1/login`, поэтому панель не дублирует логику.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter(tags=["ui"])

ACCOUNTS_FILE = Path("var/accounts.json")
SESSIONS_FILE = Path("var/sessions.json")
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
