"""Дополнение разделов README про массовый ввоз."""

from __future__ import annotations

import pathlib

RU_OLD = """* вход по логину и паролю — живой HTTP-путь без капчи;
* банк сессий: состояние, проверка одной кнопкой, копирование cookie;
* очередь и последние задания со статусами и шагами;
* счётчики воркеров, очереди и успешных входов."""

RU_NEW = """* вход по логину и паролю — живой HTTP-путь без капчи;
* **массовый ввоз**: файл или текст `логин:пароль`, список прокси, запуск пачкой;
* банк сессий: состояние, проверка одной кнопкой, копирование cookie;
* очередь и последние задания со статусами и шагами;
* счётчики воркеров, очереди и успешных входов.

Импорт понимает `логин:пароль`, `логин;пароль` и `логин<TAB>пароль`;
строки с `#` пропускаются, пароль может содержать двоеточие. Повторный
импорт обновляет пароль, а не создаёт дубликат. Прокси принимаются в виде
`http://user:pass@host:port`, `socks5://...` или просто `host:port` —
схема по умолчанию `http`."""

EN_OLD = """* sign-in by login and password — the live HTTP path with no captcha;
* session bank: state, one-click verification, cookie copy;
* queue and recent jobs with statuses and steps;
* live counters for workers, queue depth and successful sign-ins."""

EN_NEW = """* sign-in by login and password — the live HTTP path with no captcha;
* **bulk import**: a file or text of `login:password`, a proxy list, batch runs;
* session bank: state, one-click verification, cookie copy;
* queue and recent jobs with statuses and steps;
* live counters for workers, queue depth and successful sign-ins.

The importer accepts `login:password`, `login;password` and
`login<TAB>password`; `#` lines are skipped and the password may contain a
colon. Re-importing updates the password instead of creating a duplicate.
Proxies may be `http://user:pass@host:port`, `socks5://...` or plain
`host:port` — the default scheme is `http`."""

for name, old, new in (
    ("README.md", RU_OLD, RU_NEW),
    ("README.en.md", EN_OLD, EN_NEW),
):
    path = pathlib.Path(name)
    text = path.read_text(encoding="utf-8")
    if "массовый ввоз" in text.lower() or "bulk import" in text.lower():
        print(f"{name}: уже есть")
        continue
    if old not in text:
        print(f"{name}: шаблон не найден")
        continue
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"{name}: обновлён")
