"""Дополнение панели: кнопка копирования cookie в таблице банка."""

from __future__ import annotations

import pathlib

P = pathlib.Path("api/ui.py")
t = P.read_text(encoding="utf-8")

# 1. Заголовок таблицы банка.
t = t.replace(
    "<thead><tr><th>Аккаунт</th><th>Состояние</th><th>Проверено</th></tr></thead>",
    "<thead><tr><th>Аккаунт</th><th>Состояние</th><th>Проверено</th><th></th></tr></thead>",
)

# 2. Строка таблицы: кнопка копирования.
OLD_ROW = '''        <td class="muted mono">${(row.verified_at || "").slice(0, 16) || "—"}</td>
      </tr>`).join("");'''
NEW_ROW = '''        <td class="muted mono">${(row.verified_at || "").slice(0, 16) || "—"}</td>
        <td style="text-align:right"><button class="copy" data-login="${row.login}">cookie</button></td>
      </tr>`).join("");'''
t = t.replace(OLD_ROW, NEW_ROW, 1)

# 3. Пустая таблица тоже на четыре колонки.
t = t.replace(
    '''body.innerHTML = '<tr><td colspan="3" class="muted">банк пуст</td></tr>';''',
    '''body.innerHTML = '<tr><td colspan="4" class="muted">банк пуст</td></tr>';''',
)

# 4. Стиль маленькой кнопки.
CSS = '''  button.copy {
    width: auto; margin: 0; padding: 4px 10px; font-size: 11.5px; font-weight: 500;
    background: #21262d; border: 1px solid var(--stroke); color: var(--muted);
  }
  button.copy:hover { color: var(--text); border-color: var(--accent); }
  .route { margin-top: 18px;'''
if "button.copy {" not in t:
    t = t.replace("  .route { margin-top: 18px;", CSS, 1)

# 5. Обработчик копирования.
HANDLER = '''
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

const tick = () => {'''
if 'closest(".copy")' not in t:
    t = t.replace("\nconst tick = () => {", HANDLER, 1)

P.write_text(t, encoding="utf-8")
print("кнопка:", 'class="copy"' in t, "| обработчик:", 'closest(".copy")' in t)
