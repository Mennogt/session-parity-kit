"""Генерация графиков для документации без внешних зависимостей.

Пишет SVG прямо в docs/images/: он чёткий на любом экране, не требует
библиотек и нормально смотрится и в светлой, и в тёмной теме GitHub.

    python -m tools.make_charts
"""

from __future__ import annotations

from pathlib import Path

OUT = Path("docs/images")
BG = "#ffffff"
FG = "#1f2328"
MUTED = "#656d76"
ACCENT = "#2f81f7"
WARN = "#bf8700"
BAD = "#cf222e"
GOOD = "#1a7f37"


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def bar_chart(title: str, subtitle: str, rows: list[tuple[str, float, str, str]],
              width: int = 860, row_h: int = 54, max_value: float | None = None) -> str:
    """Горизонтальные полосы: (подпись, значение, подпись значения, цвет)."""
    left = 250
    right = width - 120
    height = 96 + row_h * len(rows) + 30
    peak = max_value or max(v for _, v, _, _ in rows) or 1

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="{_escape(title)}">',
        f'<rect width="{width}" height="{height}" fill="{BG}"/>',
        f'<text x="32" y="44" font-family="Segoe UI, Helvetica, Arial, sans-serif" '
        f'font-size="21" font-weight="600" fill="{FG}">{_escape(title)}</text>',
        f'<text x="32" y="70" font-family="Segoe UI, Helvetica, Arial, sans-serif" '
        f'font-size="13" fill="{MUTED}">{_escape(subtitle)}</text>',
    ]
    for i, (label, value, value_text, color) in enumerate(rows):
        y = 96 + i * row_h
        bar_w = max(2.0, (right - left) * value / peak)
        parts.append(
            f'<text x="32" y="{y + 20}" font-family="Segoe UI, Helvetica, Arial, sans-serif" '
            f'font-size="14" fill="{FG}">{_escape(label)}</text>'
        )
        parts.append(
            f'<rect x="{left}" y="{y + 6}" width="{right - left}" height="20" rx="5" '
            f'fill="#eef1f4"/>'
        )
        parts.append(
            f'<rect x="{left}" y="{y + 6}" width="{bar_w:.1f}" height="20" rx="5" '
            f'fill="{color}"/>'
        )
        parts.append(
            f'<text x="{left + bar_w + 12:.1f}" y="{y + 21}" '
            f'font-family="Consolas, Menlo, monospace" font-size="13" '
            f'fill="{MUTED}">{_escape(value_text)}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


def chain_diagram(width: int = 860, height: int = 210) -> str:
    steps = [
        ("Вход / Sign-in", "2 запроса", ACCENT),
        ("proofofwork", "1.3 с", GOOD),
        ("captchav2", "PerimeterX", BAD),
        ("сессия", "нет", MUTED),
    ]
    box_w, box_h = 176, 74
    gap = (width - 64 - box_w * len(steps)) / (len(steps) - 1)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="challenge chain">',
        f'<rect width="{width}" height="{height}" fill="{BG}"/>',
        f'<text x="32" y="40" font-family="Segoe UI, Helvetica, Arial, sans-serif" '
        f'font-size="20" font-weight="600" fill="{FG}">Цепочка проверок</text>',
        f'<text x="32" y="64" font-family="Segoe UI, Helvetica, Arial, sans-serif" '
        f'font-size="13" fill="{MUTED}">первая ступень решается в HTTP, вторая — нет</text>',
    ]
    for i, (label, note, color) in enumerate(steps):
        x = 32 + i * (box_w + gap)
        y = 92
        parts.append(
            f'<rect x="{x:.1f}" y="{y}" width="{box_w}" height="{box_h}" rx="10" '
            f'fill="#f6f8fa" stroke="{color}" stroke-width="2"/>'
        )
        parts.append(
            f'<text x="{x + box_w / 2:.1f}" y="{y + 30}" text-anchor="middle" '
            f'font-family="Segoe UI, Helvetica, Arial, sans-serif" font-size="15" '
            f'font-weight="600" fill="{FG}">{_escape(label)}</text>'
        )
        parts.append(
            f'<text x="{x + box_w / 2:.1f}" y="{y + 52}" text-anchor="middle" '
            f'font-family="Consolas, Menlo, monospace" font-size="12" '
            f'fill="{color}">{_escape(note)}</text>'
        )
        if i < len(steps) - 1:
            ax = x + box_w + 6
            parts.append(
                f'<path d="M {ax:.1f} {y + box_h / 2} L {ax + gap - 12:.1f} {y + box_h / 2}" '
                f'stroke="{MUTED}" stroke-width="2" marker-end="url(#arrow)"/>'
            )
    parts.insert(1, (
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{MUTED}"/></marker></defs>'
    ))
    parts.append("</svg>")
    return "\n".join(parts)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    (OUT / "outcomes.svg").write_text(bar_chart(
        "Исходы входа по 41 аккаунту",
        "один вход на аккаунт, изолированный клиент, без решения задач",
        [
            ("Вход без проверки", 2, "2  (4.9 %)", GOOD),
            ("Требуется proof-of-work", 39, "39  (95.1 %)", WARN),
        ],
    ), encoding="utf-8")

    (OUT / "pow-difficulty.svg").write_text(bar_chart(
        "Сложность proof-of-work как индикатор риска",
        "T — число последовательных возведений в квадрат; шкала логарифмическая",
        [
            ("Нетронутый аккаунт", 5.6, "T = 400 000   →   1.3 с", GOOD),
            ("После серии попыток", 7.7, "T = 50 000 000 → 167 с", BAD),
        ],
    ), encoding="utf-8")

    (OUT / "throughput.svg").write_text(bar_chart(
        "Пропускная способность сервиса",
        "4 воркера, SQLite; разница только в способе открытия соединения",
        [
            ("WAL на каждом соединении", 94.8, "94.8 задания/мин", BAD),
            ("WAL один раз, соединение на поток", 391.7, "391.7 задания/мин", GOOD),
        ],
    ), encoding="utf-8")

    (OUT / "chain.svg").write_text(chain_diagram(), encoding="utf-8")
    for path in sorted(OUT.glob("*.svg")):
        print(f"{path}  {path.stat().st_size} байт")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
