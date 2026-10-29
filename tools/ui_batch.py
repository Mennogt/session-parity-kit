"""Массовый ввоз: аккаунты из txt, прокси из txt, запуск пачкой."""

from __future__ import annotations

import pathlib

P = pathlib.Path("api/ui.py")
t = P.read_text(encoding="utf-8")

BACKEND = '''
ACCOUNTS_SEPARATORS = (":", ";", "\\t")


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
    merged = _parse_proxies("\\n".join(existing + rows))
    PROXIES_FILE.write_text("\\n".join(merged) + "\\n", encoding="utf-8")
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


@router.get("/ui/overview", include_in_schema=False)'''

t = t.replace('@router.get("/ui/overview", include_in_schema=False)', BACKEND, 1)

# пути к файлам
t = t.replace(
    'SESSIONS_FILE = Path("var/sessions.json")',
    'SESSIONS_FILE = Path("var/sessions.json")\n'
    'PROXIES_FILE = Path("var/proxies.txt")\n'
    'PROFILES_FILE = Path("var/profiles.json")',
)

PANEL = '''
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

  <section class="panel" style="margin-top:16px">'''

t = t.replace('\n  <section class="panel" style="margin-top:16px">', PANEL, 1)

CSS = '''  textarea {
    width: 100%; padding: 11px 12px; border-radius: 10px; border: 1px solid var(--stroke);
    background: #0f141b; color: var(--text); font: 13px/1.5 Consolas, Menlo, monospace;
    resize: vertical; outline: none;
  }
  textarea:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(47,129,247,.18); }
  .fileline { display: flex; gap: 10px; align-items: center; margin-top: 10px; }
  .fileline input[type=file] { color: var(--muted); font-size: 12px; max-width: 55%; }
  button.small { margin: 0; padding: 8px 12px; width: auto; font-size: 12.5px; }
  .route { margin-top: 18px;'''
t = t.replace('  .route { margin-top: 18px;', CSS, 1)

JS = '''
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

const tick = () => {'''
if 'id="batchAll"' in t and '$("batchAll").addEventListener' not in t:
    t = t.replace("\nconst tick = () => {", JS, 1)

P.write_text(t, encoding="utf-8")
print("бэкенд:", "/ui/accounts/import" in t, "| панель:", 'id="batchAll"' in t, "| js:", 'batchAll").addEventListener' in t)
