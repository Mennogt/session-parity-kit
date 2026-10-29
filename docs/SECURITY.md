# Безопасность / Security

Документ на двух языках: сначала русская часть, затем английская.

---

# Русская часть

## 1. Модель угроз

Репозиторий публикуется. Значит любой файл, попавший в коммит, считается
публичным навсегда: удаление следующим коммитом не отменяет историю.

Чувствительные данные проекта:

| Данные | Где лежат | Риск при публикации |
|---|---|---|
| пары «логин + пароль» | `var/accounts.json` | прямой захват аккаунтов |
| cookie входа | `var/sessions.json`, `var/session-*.json` | вход без пароля |
| адреса прокси с паролем | `var/proxies.txt`, `var/mobile-urls.txt` | расход чужого трафика и денег |
| закрытый ключ подписи | `var/sai-key.pem` | подделка подписи устройства |
| базы и журналы | `var/*.db`, `var/*.log` | история действий, профили |
| сырые результаты разведки | `var/*.json` | адреса, идентификаторы, состояния |

## 2. Что закрыто в репозитории

* `.gitignore` исключает `var/*` целиком, кроме `var/README.md` и
  `var/templates/`.
* Исключены `*.pem`, `*.key`, `.env`, `*.db`, `*.log`, `fixtures/`.
* В документации имена аккаунтов заменены обозначениями (`Аккаунт A`,
  `Аккаунт B`), значения cookie не приводятся.
* Шаблоны в `var/templates/` содержат только вымышленные значения.

## 3. Проверка перед публикацией

```powershell
# 1. Что вообще попадает в индекс
git ls-files

# 2. Поиск известных секретов в истории
git log -p | Select-String -Pattern "ROBLOSECURITY|password|proxy|:.*@|_px3"

# 3. Поиск в текущем дереве
git grep -n -I -E "ROBLOSECURITY|nigerr|\.pem|@.*:[0-9]{2,5}$"
```

Ожидаемый результат: совпадения только в документации и только как имена
полей (`ROBLOSECURITY` как название cookie), без значений.

## 4. Если секрет всё же попал в коммит

1. Немедленно отозвать: завершить сессии, сменить пароли, перевыпустить
   прокси-логин.
2. Переписать историю (`git filter-repo` или `BFG`) и сделать force-push.
3. Считать старые значения скомпрометированными даже после перезаписи:
   клоны и форки сохраняют их.

## 5. Обращение с данными во время работы

* банк сессий хранится локально, файл `var/sessions.json` не публикуется;
* cookie в ответах API — действующий секрет, API слушает `127.0.0.1`;
* зонды пишут результаты в `var/`, а не в `docs/`;
* при передаче результатов в переписку значения cookie и паролей заменяются.

## 6. Область применения

Проект предназначен для работы с собственными аккаунтами и собственной
инфраструктурой: проверка своих сессий, измерение поведения платформы,
оценка устойчивости своего сервиса.

В репозитории не должно быть чужих учётных данных ни в каком виде.

---

# English part

## 1. Threat model

The repository is public. Any file that reaches a commit is public forever:
a follow-up deletion does not rewrite history.

Sensitive project data:

| Data | Location | Risk if published |
|---|---|---|
| login/password pairs | `var/accounts.json` | direct account takeover |
| sign-in cookies | `var/sessions.json`, `var/session-*.json` | passwordless access |
| proxy endpoints with credentials | `var/proxies.txt`, `var/mobile-urls.txt` | traffic and money theft |
| signing private key | `var/sai-key.pem` | device signature forgery |
| databases and logs | `var/*.db`, `var/*.log` | action history, profiles |
| raw research output | `var/*.json` | addresses, identifiers, states |

## 2. What the repository excludes

* `.gitignore` excludes `var/*` entirely except `var/README.md` and
  `var/templates/`.
* `*.pem`, `*.key`, `.env`, `*.db`, `*.log`, `fixtures/` are excluded.
* Documentation uses placeholders (`Account A`, `Account B`) and never prints
  cookie values.
* Templates in `var/templates/` contain fictitious values only.

## 3. Pre-publication checks

```powershell
# 1. What is actually staged
git ls-files

# 2. Search history for known secrets
git log -p | Select-String -Pattern "ROBLOSECURITY|password|proxy|:.*@|_px3"

# 3. Search the current tree
git grep -n -I -E "ROBLOSECURITY|nigerr|\.pem|@.*:[0-9]{2,5}$"
```

Expected: matches only in documentation and only as field names
(`ROBLOSECURITY` as a cookie name), with no values.

## 4. If a secret reached a commit

1. Revoke immediately: terminate sessions, change passwords, rotate proxy
   credentials.
2. Rewrite history (`git filter-repo` or `BFG`) and force-push.
3. Treat the old values as compromised even after rewriting: clones and forks
   keep them.

## 5. Handling data during work

* the session bank stays local; `var/sessions.json` is never published;
* cookies returned by the API are live secrets; the API binds to `127.0.0.1`;
* probes write to `var/`, never to `docs/`;
* when sharing results, replace cookie and password values.

## 6. Intended use

The project is meant for your own accounts and infrastructure: verifying your
sessions, measuring platform behaviour, assessing your own service.

No third-party credentials may appear in this repository in any form.
