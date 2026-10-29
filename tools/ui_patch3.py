"""Последние штрихи панели: стиль выбора файла."""

from __future__ import annotations

import pathlib

P = pathlib.Path("api/ui.py")
t = P.read_text(encoding="utf-8")

CSS = '''  .fileline input[type=file]::file-selector-button {
    background: #21262d; color: var(--text); border: 1px solid var(--stroke);
    border-radius: 8px; padding: 7px 12px; margin-right: 10px; cursor: pointer;
    font: inherit; font-size: 12px;
  }
  .fileline input[type=file]::file-selector-button:hover { border-color: var(--accent); }
  textarea {'''
if "file-selector-button" not in t:
    t = t.replace("  textarea {", CSS, 1)

P.write_text(t, encoding="utf-8")
print("стиль выбора файла:", "file-selector-button" in t)
