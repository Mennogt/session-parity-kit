"""Поиск и чтение страниц без API-ключей.

Бэкенды по порядку: DuckDuckGo HTML, Bing. Возвращает заголовок и ссылку.
Чтение — вытаскивает текст из HTML, без внешних зависимостей.

    python -m research.search "запрос" 8
    python -m research.fetch "https://..." 6000
"""

from __future__ import annotations

import html
import re
import sys
from urllib.parse import quote_plus, unquote, urlparse, parse_qs

import httpx

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
}


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _ddg(query: str, count: int) -> list[dict]:
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    with httpx.Client(timeout=30, headers=HEADERS, follow_redirects=True) as client:
        resp = client.get(url)
    if resp.status_code != 200:
        return []
    out: list[dict] = []
    for match in re.finditer(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', resp.text, re.S
    ):
        href, title = match.group(1), _clean(match.group(2))
        if "uddg=" in href:
            qs = parse_qs(urlparse(href).query)
            href = unquote(qs.get("uddg", [href])[0])
        if href.startswith("http"):
            out.append({"title": title, "url": href, "backend": "ddg"})
        if len(out) >= count:
            break
    return out


def _bing(query: str, count: int) -> list[dict]:
    url = f"https://www.bing.com/search?q={quote_plus(query)}&setlang=en"
    with httpx.Client(timeout=30, headers=HEADERS, follow_redirects=True) as client:
        resp = client.get(url)
    if resp.status_code != 200:
        return []
    out: list[dict] = []
    for match in re.finditer(
        r'<li class="b_algo".*?<h2><a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', resp.text, re.S
    ):
        href, title = match.group(1), _clean(match.group(2))
        if href.startswith("http"):
            out.append({"title": title, "url": href, "backend": "bing"})
        if len(out) >= count:
            break
    return out


def search(query: str, count: int = 8) -> list[dict]:
    for backend in (_ddg, _bing):
        try:
            found = backend(query, count)
        except Exception as exc:
            print(f"[{backend.__name__}] ошибка: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        if found:
            return found
    return []


def fetch(url: str, limit: int = 6000) -> str:
    with httpx.Client(timeout=40, headers=HEADERS, follow_redirects=True) as client:
        resp = client.get(url)
    head = f"HTTP {resp.status_code} {url}\n"
    body = resp.text
    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", body, re.S | re.I)
    if m:
        title = _clean(m.group(1))
    body = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", body)
    body = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", body)
    text = _clean(body)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return f"{head}TITLE: {title}\n\n{text[:limit]}"


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    mode, arg = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "")
    if mode not in ("search", "fetch"):
        # Короткая форма: python -m research.search "запрос" 8
        arg, mode = mode, "search"
        count = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 8
        results = search(arg, count)
        if not results:
            print("ничего не найдено (бэкенды вернули пусто или заглушку)")
            return 3
        for i, r in enumerate(results, 1):
            print(f"{i}. {r['title']}\n   {r['url']}  [{r['backend']}]")
        return 0
    if mode == "search":
        count = int(sys.argv[3]) if len(sys.argv) > 3 else 8
        results = search(arg, count)
        if not results:
            print("ничего не найдено (бэкенды вернули пусто или заглушку)")
            return 3
        for i, r in enumerate(results, 1):
            print(f"{i}. {r['title']}\n   {r['url']}  [{r['backend']}]")
        return 0
    if mode == "fetch":
        limit = int(sys.argv[3]) if len(sys.argv) > 3 else 6000
        print(fetch(arg, limit))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
