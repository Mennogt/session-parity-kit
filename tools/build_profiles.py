"""Сборка пула профилей из проверенных адресов.

На вход — результат `probes.ip_score` (var/ip-score.json) или список портов.
На выход — var/profiles.json, где остались только адреса с вердиктом
`trusted`. Помеченные адреса в пул не попадают: на них вход требует
проверки, которую HTTP-путь пройти не может.

    python -m tools.build_profiles --from-score var/ip-score.json
    python -m tools.build_profiles --direct            # один профиль без прокси
    python -m tools.build_profiles --ports 1,2,3 --ports-file var/trusted-ports.txt
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
HEADERS = {
    "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}


def proxy_url(port: int) -> str:
    from probes.proxy_check import PASSWORD, USER, HOST

    return f"http://{USER}:{PASSWORD}@{HOST}:{port}"


def make_profile(idx: int, port: int | None) -> dict:
    return {
        "id": f"p{idx:03d}" if port is not None else f"direct-{idx:03d}",
        "proxy": proxy_url(port) if port is not None else None,
        "user_agent": UA,
        "accept_language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "headers": HEADERS,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-score", default="var/ip-score.json")
    ap.add_argument("--ports", default=None, help="явный список портов через запятую")
    ap.add_argument("--direct", action="store_true", help="профиль без прокси")
    ap.add_argument("--direct-count", type=int, default=1)
    ap.add_argument("--out", default="var/profiles.json")
    args = ap.parse_args()

    ports: list[int] = []
    if args.ports:
        ports = [int(p) for p in args.ports.split(",") if p.strip()]
    elif not args.direct:
        source = Path(args.from_score)
        if not source.exists():
            print(f"нет файла {source}; сначала запусти probes.ip_score")
            return 2
        rows = json.loads(source.read_text(encoding="utf-8"))
        ports = [int(r["port"]) for r in rows if r.get("verdict") == "trusted"]
        rejected = len(rows) - len(ports)
        print(f"из {len(rows)} адресов годных {len(ports)}, отброшено {rejected}")

    profiles = [make_profile(i, p) for i, p in enumerate(ports, 1)]
    if args.direct:
        profiles += [make_profile(0, None) for _ in range(args.direct_count)]

    if not profiles:
        print("пул пуст: ни одного доверенного адреса. Профили не перезаписаны.")
        return 3

    Path(args.out).write_text(
        json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"записано профилей: {len(profiles)} -> {args.out}")
    for p in profiles[:5]:
        print(f"  {p['id']}  proxy={'да' if p['proxy'] else 'нет'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
