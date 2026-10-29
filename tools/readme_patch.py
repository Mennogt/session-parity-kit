"""Раздел про панель в README (RU и EN)."""

from __future__ import annotations

import pathlib

RU = """## Панель

Одна команда поднимает сервис и открывает браузер:

```bash
python -m tools.launch
```

![Панель](docs/images/ui.png)

Что внутри:

* вход по логину и паролю — живой HTTP-путь без капчи;
* банк сессий: состояние, проверка одной кнопкой, копирование cookie;
* очередь и последние задания со статусами и шагами;
* счётчики воркеров, очереди и успешных входов.

Панель слушает `127.0.0.1` и работает без сборки и внешних зависимостей.

"""

EN = """## Dashboard

One command starts everything and opens the browser:

```bash
python -m tools.launch
```

![Dashboard](docs/images/ui.png)

What it gives you:

* sign-in by login and password — the live HTTP path with no captcha;
* session bank: state, one-click verification, cookie copy;
* queue and recent jobs with statuses and steps;
* live counters for workers, queue depth and successful sign-ins.

The dashboard binds to `127.0.0.1` and needs no build step or extra
dependencies.

"""

for name, block, anchor in (
    ("README.md", RU, "## Быстрый старт"),
    ("README.en.md", EN, "## Quick start"),
):
    path = pathlib.Path(name)
    text = path.read_text(encoding="utf-8")
    if "## Панель" in text or "## Dashboard" in text:
        print(f"{name}: раздел уже есть")
        continue
    text = text.replace(anchor, block + anchor, 1)
    path.write_text(text, encoding="utf-8")
    print(f"{name}: раздел добавлен")
