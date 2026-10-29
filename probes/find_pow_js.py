"""Поиск логики proof-of-work в клиентских бандлах Roblox.

Страница входа подключает скрипты; в них должен быть код, который считает
PoW и отправляет его на сервер. Скачиваем бандлы обычным HTTP и ищем по
строкам: `proofofwork`, `challenge/v1`, `difficulty`, `nonce`.

    python -m probes.find_pow_js
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urljoin

import httpx

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)
OUT = Path("var/js")
KEYS = ("proofofwork", "proof_of_work", "challenge/v1", "difficulty",
        "redemptionToken", "rblx-challenge", "getServerNonce", "secureAuthenticationIntent")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="https://www.roblox.com/login")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--out", default="var/pow-js-hits.json")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=60, follow_redirects=True,
                      headers={"User-Agent": UA, "Accept": "text/html"}) as client:
        page = client.get(args.url)
        print(f"страница: {page.status_code}, {len(page.content)} байт")
        scripts = re.findall(r'<script[^>]+src="([^"]+)"', page.text)
        scripts += re.findall(r'"(https://[^"]+?\.js)"', page.text)
        urls = []
        for src in scripts:
            full = urljoin(args.url, src)
            if full not in urls and full.endswith(".js"):
                urls.append(full)
        print(f"скриптов найдено: {len(urls)}")

        hits: list[dict] = []
        for i, url in enumerate(urls[: args.limit], 1):
            try:
                resp = client.get(url)
            except Exception as exc:
                print(f"  {i:>2} ошибка {type(exc).__name__}: {url[:80]}")
                continue
            text = resp.text
            name = re.sub(r"[^A-Za-z0-9._-]", "_", url.split("/")[-1])[:60]
            (OUT / f"{i:02d}-{name}").write_text(text, encoding="utf-8")
            found = [k for k in KEYS if k in text]
            print(f"  {i:>2} {resp.status_code} {len(text):>8} байт "
                  f"{'НАЙДЕНО: ' + ','.join(found) if found else ''} {url[:70]}")
            if found:
                hits.append({"url": url, "size": len(text), "keys": found,
                             "file": str(OUT / f"{i:02d}-{name}")})

        Path(args.out).write_text(json.dumps(hits, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        print(f"\nфайлов с совпадениями: {len(hits)}")
        for h in hits:
            print(f"  {h['keys']} -> {h['file']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
