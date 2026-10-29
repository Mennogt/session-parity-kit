"""Публикация репозитория на GitHub через device flow и REST API.

Git и GitHub CLI не нужны: авторизация идёт по протоколу OAuth device flow,
файлы загружаются через Git Data API (blob → tree → commit → ref).

    python -m tools.publish_github auth
    python -m tools.publish_github publish --repo session-parity-kit
    python -m tools.publish_github status

Токен хранится в var/github-token.json (каталог исключён из публикации).
"""

from __future__ import annotations

import argparse
import base64
import json
import time
from pathlib import Path

import httpx

# Публичный client_id GitHub CLI: device flow включён, права ограничены scope.
CLIENT_ID = "178c6fc778ccc68e1d6a"
SCOPES = "repo workflow read:user"
TOKEN_FILE = Path("var/github-token.json")

DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
API = "https://api.github.com"

# Что не публикуем никогда.
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "node_modules",
             "var", "fixtures"}
SKIP_SUFFIXES = {".db", ".db-wal", ".db-shm", ".log", ".pem", ".key", ".pyc"}
SKIP_FILES = {".env", "fixup_encoding.py"}


def device_login(timeout_minutes: int = 15) -> dict:
    with httpx.Client(timeout=40, headers={"Accept": "application/json"}) as client:
        r = client.post(DEVICE_CODE_URL, data={"client_id": CLIENT_ID, "scope": SCOPES})
        r.raise_for_status()
        data = r.json()
        print("=" * 60)
        print(f"  Код: {data['user_code']}")
        print(f"  Открой: {data['verification_uri']}")
        print(f"  Действует {data['expires_in'] // 60} минут")
        print("=" * 60, flush=True)

        interval = int(data.get("interval", 5))
        deadline = time.monotonic() + timeout_minutes * 60
        while time.monotonic() < deadline:
            time.sleep(interval)
            poll = client.post(ACCESS_TOKEN_URL, data={
                "client_id": CLIENT_ID,
                "device_code": data["device_code"],
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            }).json()
            if poll.get("access_token"):
                token = poll["access_token"]
                TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
                TOKEN_FILE.write_text(json.dumps({"access_token": token}), encoding="utf-8")
                who = client.get(f"{API}/user",
                                 headers={"Authorization": f"Bearer {token}",
                                          "Accept": "application/vnd.github+json"}).json()
                print(f"вошли как {who.get('login')}", flush=True)
                return {"token": token, "login": who.get("login")}
            error = poll.get("error", "")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            print(f"остановка: {error} {poll.get('error_description', '')}", flush=True)
            break
    return {}


def load_token() -> str:
    if not TOKEN_FILE.exists():
        return ""
    return json.loads(TOKEN_FILE.read_text(encoding="utf-8")).get("access_token", "")


def collect_files(root: Path) -> list[tuple[str, bytes]]:
    files: list[tuple[str, bytes]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if set(rel.parts) & SKIP_DIRS:
            continue
        if rel.parts[:2] == (".github", "workflows"):
            # GitHub не даёт создавать workflow-файлы без scope `workflow`
            # и отвечает 404. Такой файл добавляется вручную или повторной
            # авторизацией с этим правом.
            print(f"  пропущен workflow (нужен scope workflow): {rel.as_posix()}")
            continue
        if path.suffix.lower() in SKIP_SUFFIXES or path.name in SKIP_FILES:
            continue
        if path.suffix.lower() == ".png" and "docs/images" not in rel.as_posix():
            continue
        if rel.parts and rel.parts[0].startswith(".") and rel.parts[0] not in {".github", ".gitattributes", ".gitignore", ".env.example"}:
            continue
        if path.stat().st_size > 2_000_000:
            print(f"  пропущен крупный файл: {rel}")
            continue
        files.append((rel.as_posix(), path.read_bytes()))
    return files


def publish(repo: str, token: str, private: bool, description: str,
            topics: list[str], force: bool = False) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    with httpx.Client(timeout=180, headers=headers) as client:
        who = client.get(f"{API}/user").json()
        owner = who.get("login")
        print(f"владелец: {owner}", flush=True)

        existing = client.get(f"{API}/repos/{owner}/{repo}")
        created_now = False
        base_sha = ""
        if existing.status_code == 200:
            full = existing.json()["full_name"]
            commits = client.get(f"{API}/repos/{full}/commits")
            if commits.status_code == 200 and commits.json():
                if not force:
                    print(f"репозиторий уже содержит коммиты: {existing.json()['html_url']}")
                    return {"url": existing.json()["html_url"], "created": False}
                base_sha = commits.json()[0]["sha"]
                print(f"обновляем существующий репозиторий от {base_sha[:10]}", flush=True)
            else:
                print(f"репозиторий существует и пуст: {existing.json()['html_url']}")
        else:
            created = client.post(f"{API}/user/repos", json={
                "name": repo, "private": private, "description": description,
                "auto_init": False, "has_issues": True, "has_wiki": False,
            })
            if created.status_code not in (200, 201):
                print("не удалось создать репозиторий:", created.status_code, created.text[:300])
                return {}
            full = created.json()["full_name"]
            created_now = True
            print(f"создан: {created.json()['html_url']}", flush=True)

        files = collect_files(Path("."))
        total = sum(len(content) for _, content in files)
        print(f"файлов к загрузке: {len(files)}, {total / 1024:.0f} КБ", flush=True)

        # В пустом репозитории GitHub не даёт создать дерево (409), поэтому
        # сначала кладём один файл через Contents API — он делает начальный
        # коммит и ветку, — а затем заменяем дерево целиком.
        if not base_sha:
            bootstrap = next(((p, c) for p, c in files if p == "README.md"),
                             (files[0][0], files[0][1]))
            put = client.put(f"{API}/repos/{full}/contents/{bootstrap[0]}", json={
                "message": "chore: bootstrap repository",
                "content": base64.b64encode(bootstrap[1]).decode(),
            })
            if put.status_code not in (200, 201):
                print("начальный коммит не создан:", put.status_code, put.text[:300])
                return {}
            base_sha = put.json()["commit"]["sha"]
            print(f"начальный коммит: {base_sha[:10]}", flush=True)

        # Крупное дерево одним запросом GitHub не принимает, поэтому каждый
        # файл загружается как blob, а дерево собирается из ссылок.
        tree_entries = []
        for index, (path, content) in enumerate(files, 1):
            blob = client.post(f"{API}/repos/{full}/git/blobs", json={
                "content": base64.b64encode(content).decode(),
                "encoding": "base64",
            })
            if blob.status_code not in (200, 201):
                print(f"blob не создан для {path}: {blob.status_code} {blob.text[:120]}")
                return {}
            tree_entries.append({
                "path": path, "mode": "100644", "type": "blob",
                "sha": blob.json()["sha"],
            })
            if index % 25 == 0:
                print(f"  загружено {index}/{len(files)}", flush=True)

        tree = client.post(f"{API}/repos/{full}/git/trees", json={"tree": tree_entries})
        if tree.status_code not in (200, 201):
            print("дерево не создано:", tree.status_code, tree.text[:300])
            return {}
        tree_sha = tree.json()["sha"]
        print(f"дерево: {tree_sha[:10]}", flush=True)

        commit = client.post(f"{API}/repos/{full}/git/commits", json={
            "message": "Initial public release",
            "tree": tree_sha,
            "parents": [base_sha],
        })
        if commit.status_code not in (200, 201):
            print("коммит не создан:", commit.status_code, commit.text[:300])
            return {}
        commit_sha = commit.json()["sha"]

        ref = client.patch(f"{API}/repos/{full}/git/refs/heads/main",
                           json={"sha": commit_sha, "force": True})
        if ref.status_code not in (200, 201):
            print("ветка не обновлена:", ref.status_code, ref.text[:300])
            return {}

        client.patch(f"{API}/repos/{full}", json={"default_branch": "main"})
        if topics:
            client.put(f"{API}/repos/{full}/topics",
                       json={"names": topics},
                       headers={**headers, "Accept": "application/vnd.github.mercy-preview+json"})

        url = f"https://github.com/{full}"
        print(f"опубликовано: {url}", flush=True)
        return {"url": url, "created": True, "files": len(files), "commit": commit_sha}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["auth", "publish", "status"])
    ap.add_argument("--repo", default="session-parity-kit")
    ap.add_argument("--private", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="перезаписать дерево в существующем репозитории")
    ap.add_argument(
        "--description",
        default="Research toolkit and queue-backed service for session parity work")
    args = ap.parse_args()

    if args.action == "auth":
        return 0 if device_login() else 1

    token = load_token()
    if not token:
        print("нет токена: сначала выполните auth")
        return 1

    if args.action == "status":
        with httpx.Client(timeout=40, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }) as client:
            who = client.get(f"{API}/user")
            print("пользователь:", who.json().get("login"), "| код", who.status_code)
        return 0

    who = httpx.get(f"{API}/user", headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json"}, timeout=40).json().get("login")
    result = publish(args.repo, token, args.private, args.description,
                     ["research", "http", "automation", "protocol-analysis",
                      "security-research"], force=args.force)
    if result.get("url"):
        Path("var/published.json").write_text(
            json.dumps({"owner": who, **result}, ensure_ascii=False, indent=2),
            encoding="utf-8")
    return 0 if result else 1


if __name__ == "__main__":
    raise SystemExit(main())
