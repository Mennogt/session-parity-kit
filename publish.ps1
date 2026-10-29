# Публикация репозитория на GitHub одним запуском.
#    .\publish.ps1 -Repo session-parity-kit
#    .\publish.ps1 -Repo session-parity-kit -Private
param(
    [string]$Repo = "session-parity-kit",
    [string]$Org = "",
    [switch]$Private,
    [switch]$SkipChecks
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Need($name, $hint) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        Write-Host "не найден $name. $hint" -ForegroundColor Yellow
        exit 2
    }
}

Need git "Установите: winget install --id Git.Git -e --silent"

if (-not $SkipChecks) {
    Write-Host "== проверка секретов ==" -ForegroundColor Cyan
    & .\.venv\Scripts\python.exe -m tools.check_secrets
    if ($LASTEXITCODE -ne 0) {
        Write-Host "в дереве есть секреты — публикация остановлена" -ForegroundColor Red
        exit 1
    }
    Write-Host "== тесты ==" -ForegroundColor Cyan
    & .\.venv\Scripts\python.exe -m pytest -q
    if ($LASTEXITCODE -ne 0) {
        Write-Host "тесты не проходят — публикация остановлена" -ForegroundColor Red
        exit 1
    }
}

if (-not (Test-Path .git)) {
    git init | Out-Null
    git branch -M main
}

git add -A
$staged = git diff --cached --name-only
$forbidden = $staged | Where-Object { $_ -match '^(var|fixtures)/' }
if ($forbidden) {
    Write-Host "в индекс попали рабочие данные:" -ForegroundColor Red
    $forbidden | ForEach-Object { Write-Host "  $_" }
    Write-Host "уберите их: git reset HEAD <файл>" -ForegroundColor Yellow
    exit 1
}

git -c user.name="Session Parity Kit" -c user.email="noreply@example.com" `
    commit -m "Initial public release" | Out-Null

$visibility = if ($Private) { "--private" } else { "--public" }
$target = if ($Org) { "$Org/$Repo" } else { $Repo }

if (Get-Command gh -ErrorAction SilentlyContinue) {
    Write-Host "== создание репозитория ==" -ForegroundColor Cyan
    gh repo create $target $visibility --source . --remote origin --push `
        --description "Research toolkit and queue-backed service for session parity work"
    if ($LASTEXITCODE -eq 0) {
        gh repo edit --add-topic research,http,automation,protocol-analysis,security-research | Out-Null
        Write-Host "опубликовано: https://github.com/$target" -ForegroundColor Green
    }
} else {
    Write-Host "gh не найден. Осталось выполнить вручную:" -ForegroundColor Yellow
    Write-Host "  git remote add origin https://github.com/<user>/$Repo.git"
    Write-Host "  git push -u origin main"
}
