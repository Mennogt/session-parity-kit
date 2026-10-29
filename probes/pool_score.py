"""Отбор доверенных адресов из произвольного списка прокси.

Читает файл в формате `user:pass@host:port` (или `host:port:user:pass`,
или `http://...`), проверяет каждый адрес двумя запросами:

  1. живость — нейтральный эндпоинт платформы;
  2. гейт — обмен пары с **заведомо неверным** паролем у существующего
     аккаунта. Ответ `Incorrect username or password` значит «адрес прошёл
     риск-проверку», ответ `Challenge is required` — «адрес помечен».

Успешного входа не происходит, аккаунт не расходуется.

    python -m probes.pool_score --file C:\\Users\\404\\Desktop\\proxymob.txt --sample 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from pathlib import Path

import httpx

from flow.detect import classify_exchange

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
PROBE_PASSWORD = "pool_score_probe_wrong_password"
CHECK_URL = "https://apis.roblox.com/captcha/v1/metadata"
LOGIN_URL = "https://auth.roblox.com/v2/login"
HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://www.roblox.com",
    "Referer": "https://www.roblox.com/login",
}
LINE_RE = re.compile(r"^\s*\d+[:.)]\s*")  # нумерация строк в файле


def normalize(line: str) -> str | None:
    """Привести строку файла к URL прокси."""
    text = LINE_RE.sub("", line).strip()
    if not text or text.startswith("#"):
        return None
    if text.startswith(("http://", "https://")):
        return text
    # user:pass@host:port
    if "@" in text:
        creds, hostport = text.rsplit("@", 1)
        return f"http://{creds}@{hostport}"
    # host:port:user:pass
    parts = text.split(":")
    if len(parts) == 4:
        host, port, user, password = parts
        return f"http://{user}:{password}@{host}:{port}"
    return None


def load(path: Path) -> list[str]:
    out: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        url = normalize(line)
        if url:
            out.append(url)
    return out


async def check_one(proxy: str | None, login: str, timeout: float) -> dict:
    started = time.perf_counter()
    row: dict = {"proxy": (proxy or "ПРЯМОЙ").split("@")[-1], "ok": False}
    try:
        async with httpx.AsyncClient(
            proxy=proxy, timeout=httpx.Timeout(connect=timeout, read=timeout,
                                              write=timeout, pool=timeout),
            follow_redirects=False,
        ) as client:
            alive = await client.get(CHECK_URL, headers={"User-Agent": UA})
            row["alive"] = alive.status_code == 200
            body = {"ctype": "Username", "cvalue": login, "password": PROBE_PASSWORD}
            r1 = await client.post(LOGIN_URL, headers=HEADERS, json=body)
            token = r1.headers.get("x-csrf-token")
            headers = dict(HEADERS)
            if token:
                headers["X-CSRF-TOKEN"] = token
            r2 = await client.post(LOGIN_URL, headers=headers, json=body)
            cls = classify_exchange(r2.status_code, r2.headers, r2.content)
            row.update({
                "status": r2.status_code,
                "cls": str(cls.status),
                "verdict": "trusted" if "BAD_CREDENTIALS" in str(cls.status) else "flagged",
                "ok": True,
            })
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {str(exc)[:80]}"
    row["ms"] = int((time.perf_counter() - started) * 1000)
    return row


async def exit_ip(proxy: str, timeout: float) -> dict:
    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=timeout) as client:
            r = await client.get("http://ip-api.com/json/?fields=country,isp,as,mobile,proxy,hosting")
            return r.json()
    except Exception as exc:
        return {"error": type(exc).__name__}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=r"C:\Users\404\Desktop\proxymob.txt")
    ap.add_argument("--sample", type=int, default=20)
    ap.add_argument("--account", type=int, default=3)
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--out", default="var/pool-score.json")
    args = ap.parse_args()

    accounts = json.loads(Path("var/accounts.json").read_text(encoding="utf-8"))
    login = accounts[args.account]["login"]

    proxies = load(Path(args.file))
    print(f"в файле прокси: {len(proxies)}")

    # Контроль: тот же запрос напрямую с доверенного адреса. Если даже тут
    # гейт — аккаунт сейчас в паузе, и оценка адресов бессмысленна.
    control = await check_one(None, login, args.timeout)
    print(f"контроль (прямой IP): {control.get('verdict', control.get('error'))}")
    if control.get("verdict") != "trusted":
        print("аккаунт сейчас под гейтом — оценка адресов невозможна, нужно подождать")
        return 2

    sample = proxies[: args.sample]
    print(f"проверяю: {len(sample)}; логин для пробы: {login}")

    sem = asyncio.Semaphore(args.concurrency)
    results: list[dict] = []

    async def one(proxy: str) -> None:
        async with sem:
            row = await check_one(proxy, login, args.timeout)
            results.append(row)
            mark = {"trusted": "+", "flagged": "-"}.get(row.get("verdict"), "!")
            print(f"  [{mark}] {row['proxy']:<28} {row.get('verdict', row.get('error')):<8} "
                  f"{row.get('status', '-')} {row['ms']}мс")

    started = time.perf_counter()
    await asyncio.gather(*(one(p) for p in sample))

    trusted = [r for r in results if r.get("verdict") == "trusted"]
    flagged = [r for r in results if r.get("verdict") == "flagged"]
    errors = [r for r in results if "error" in r]
    print(f"\nвремя: {time.perf_counter() - started:.0f} с")
    print(f"доверенных: {len(trusted)} / {len(results)} "
          f"({len(trusted) / max(1, len(results)) * 100:.0f}%)")
    print(f"помеченных: {len(flagged)}, ошибок: {len(errors)}")

    if trusted:
        ports = [r["proxy"] for r in trusted]
        Path("var/trusted-pool.txt").write_text("\n".join(ports) + "\n", encoding="utf-8")
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        info = await exit_ip(f"http://{[p for p in proxies if p.split('@')[-1] == ports[0]][0].split('@')[0]}"
                             f"@{ports[0]}", 25) if False else await exit_ip(
            next(p for p in proxies if p.split("@")[-1] == trusted[0]["proxy"]), 25)
        print(f"\nкласс доверенного адреса {trusted[0]['proxy']}: {json.dumps(info, ensure_ascii=False)}")
        print("\nпервые доверенные:")
        for r in trusted[:10]:
            print(f"  {r['proxy']}")
    else:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
