"""Панель телеметрии: карточки, график, выбор числа воркеров."""

from __future__ import annotations

import pathlib

P = pathlib.Path("api/ui.py")
t = P.read_text(encoding="utf-8")

SECTION = '''
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
    <h2>Массовый ввоз</h2>'''

if 'id="tCpu"' not in t:
    t = t.replace('\n  <section class="panel" style="margin-top:16px">\n    <h2>Массовый ввоз</h2>', SECTION, 1)

JS = '''
const history = { cpu: [], kpm: [] };
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
  draw(history.cpu, "#2f81f7", 100);
  draw(history.kpm, "#3fb950", Math.max(1, ...history.kpm));
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
    history.cpu.push(data.cpu_total_percent || 0);
    history.kpm.push(data.logins_per_min || 0);
    if (history.cpu.length > 60) { history.cpu.shift(); history.kpm.shift(); }
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

const tick = () => {'''

if 'id="spark"' in t and "$(\"applyWorkers\")" not in t:
    t = t.replace("\nconst tick = () => {", JS, 1)

if 'loadSystem();' not in t:
    t = t.replace(
        'loadAccounts(); loadBank(); loadJobs(); loadOverview();',
        'loadAccounts(); loadBank(); loadJobs(); loadOverview(); loadSystem();',
    )
    t = t.replace(
        'setInterval(() => { loadOverview(); loadJobs(); loadBank(); }, 5000);',
        'setInterval(() => { loadOverview(); loadJobs(); loadBank(); }, 5000);\nsetInterval(loadSystem, 2000);',
    )

P.write_text(t, encoding="utf-8")
print("секция:", 'id="tCpu"' in t, "| график:", 'id="spark"' in t, "| опрос:", "setInterval(loadSystem" in t)
