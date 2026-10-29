# Публикация на GitHub / Publishing to GitHub

Git и GitHub CLI на этой машине не установлены, поэтому публикация
выполняется одним скриптом. Английская часть — ниже.

## Русский

### 1. Установить инструменты (один раз)

```powershell
winget install --id Git.Git -e --silent
winget install --id GitHub.cli -e --silent
```

После установки перезапустите терминал, чтобы обновился `PATH`.

### 2. Войти в GitHub

```powershell
gh auth login
```

Выберите `GitHub.com` → `HTTPS` → вход через браузер.

### 3. Опубликовать

```powershell
.\publish.ps1 -Repo session-parity-kit
```

Скрипт:

* проверит, что `var/` и `fixtures/` не попадают в индекс;
* прогонит поиск секретов (cookie, ключи, прокси-креды);
* создаст репозиторий, сделает первый коммит и запушит;
* выставит описание и темы репозитория.

Параметры: `-Private` (закрытый репозиторий), `-Org <имя>` (публикация в
организации), `-SkipChecks` (пропустить проверки — не рекомендуется).

### 4. Если `gh` не используется

```powershell
git init
git add .
git commit -m "Initial public release"
git branch -M main
git remote add origin https://github.com/<user>/session-parity-kit.git
git push -u origin main
```

Токен: `Settings → Developer settings → Personal access tokens → Fine-grained`,
права `Contents: read and write` на нужный репозиторий.

### 5. После публикации

* проверьте вкладку **Security** — GitHub сам ищет секреты в новых коммитах;
* включите **Private vulnerability reporting**;
* при публикации темы (`topics`): `research`, `http`, `automation`,
  `protocol-analysis`, `security-research`.

---

## English

### 1. Install tools (once)

```powershell
winget install --id Git.Git -e --silent
winget install --id GitHub.cli -e --silent
```

Restart the terminal afterwards so `PATH` is refreshed.

### 2. Sign in

```powershell
gh auth login
```

Pick `GitHub.com` → `HTTPS` → browser login.

### 3. Publish

```powershell
.\publish.ps1 -Repo session-parity-kit
```

The script:

* verifies `var/` and `fixtures/` never reach the index;
* scans for secrets (cookies, keys, proxy credentials);
* creates the repository, makes the first commit and pushes;
* sets the repository description and topics.

Flags: `-Private`, `-Org <name>`, `-SkipChecks`.

### 4. Without `gh`

```powershell
git init
git add .
git commit -m "Initial public release"
git branch -M main
git remote add origin https://github.com/<user>/session-parity-kit.git
git push -u origin main
```

Token: `Settings → Developer settings → Personal access tokens → Fine-grained`,
scope `Contents: read and write` on the target repository.

### 5. After publishing

* check the **Security** tab — GitHub scans new commits for secrets;
* enable **Private vulnerability reporting**;
* suggested topics: `research`, `http`, `automation`,
  `protocol-analysis`, `security-research`.
