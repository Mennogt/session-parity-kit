# Proof-of-work вместо капчи — протокол вскрыт

## Главное

Тип проверки зависит от того, прислана ли подпись входа.

| Запрос | Тип проверки |
|---|---|
| без `secureAuthenticationIntent` | `CAPTCHA_MODE_INVALID`: PerimeterX, интерактивное удержание |
| **с правильной подписью** | **`proofofwork`: решается арифметикой** |

## Подпись входа

* Кривая: ECDSA P-256 (SECP256R1), хеш SHA-256.
* Сообщение: `clientPublicKey|clientEpochTimestamp|serverNonce` — **через
  вертикальные черты**, именно в таком порядке.
* `clientPublicKey` — SPKI DER в base64, ключ эфемерный (новый на попытку).
* `serverNonce` — из `GET https://apis.roblox.com/hba-service/v1/getServerNonce`.
* `saiSignature` — raw `r||s` по 32 байта, в base64.

Если порядок или разделители иные, платформа подпись не принимает и
возвращает интерактивную капчу.

## Протокол PoW

```
GET  https://apis.roblox.com/proof-of-work-service/v1/pow-puzzle?sessionID=<sessionId>
->   200
     {"puzzleType":"1",
      "artifacts":"{\"N\":\"<309-значное>\",\"A\":<int>,\"T\":400000}"}

решение = A^(2^T) mod N        // последовательное возведение в квадрат

POST https://apis.roblox.com/proof-of-work-service/v1/pow-puzzle
     {"sessionID":"<sessionId>","solution":"<решение>"}
->   200 {"answerCorrect":true,"redemptionToken":"<uuid>","message":""}
```

`solution` — десятичная строка результата.

## Замеры

| Шаг | Результат |
|---|---|
| Вход с подписью | 403 + `rblx-challenge-type: proofofwork` + метаданные |
| Получение задания | 200, один запрос |
| Решение | **1.3 с** (`pow(a, 1 << t, n)` в C-скорости) |
| Проверка решения | `answerCorrect: true`, токен получен |

Браузер, решатель и особые прокси на этом шаге не нужны.

## Вторая ступень: captchav2 — это PerimeterX

После PoW и `challenge/v1/continue` (200) платформа выдаёт `captchav2`.
Запрос `POST apis.roblox.com/v2/captcha` с его идентификатором возвращает
конфигурацию **PerimeterX**:

```
{"appId":"PXbf8PROpW","jsClientSrc":"https://client.perimeterx.net/PXbf8PROpW/main.min.js",
 "hostUrl":"https://collector-PXbf8PROpW.perimeterx.net",
 "blockScript":"https://captcha.px-cdn.net/PXbf8PROpW/captcha.js?a=c&u=..."}
```

То есть цепочка: `proofofwork` → `captchav2` (PerimeterX, интерактив).

Проверено и не работает (все варианты дают 403):

| Способ | Ответ |
|---|---|
| повтор входа без заголовков после `continue` | `Challenge is required` |
| с `x-retry-attempt` | то же |
| исходный id + `{"redemptionToken"}` | `Challenge failed to authorize` |
| полный блок метаданных | `Challenge failed to authorize` |
| адрес с `?urlLocale=en_us` | `Challenge is required` |
| токен в теле `POST auth.roblox.com/v1/login` (`challengeId`, `captchaId`, `captchaToken`, `captchaProvider`) | `Challenge is required` |

## Сложность PoW — индикатор риска

| Состояние аккаунта | `T` | Время решения |
|---|---|---|
| не тронутый | 400 000 | 1.3 с |
| после серии попыток | 50 000 000 | 167 с (и не принимается) |

Сложность привязана к аккаунту, а не к адресу: на семи разных адресах
(включая домашний) у «сожжённого» аккаунта было одинаково 50 000 000, у
нетронутого — 400 000.

## Что осталось

Вторая ступень — PerimeterX. Чистым HTTP она не снимается: нужен либо
интерактив в настоящем браузере, либо решатель, либо адрес с низким риском.

---

# Вторая ступень снята: полный проход без капчи

Позже выяснилось, что вторая ступень не требует интерактива, если идти по
протоколу до конца. Токен выдаёт сам сервер.

## Протокол

```
1. POST auth.roblox.com/v2/login + подпись SAI
   -> 403 rblx-challenge-type: proofofwork

2. GET  .../pow-puzzle?sessionID=...
   -> N, A, T;   решение = A^(2^T) mod N

3. POST .../pow-puzzle {"sessionID","solution"}
   -> {"answerCorrect":true,"redemptionToken":"<uuid>"}

4. POST .../challenge/v1/continue
   {"challengeId":"<generic>","challengeType":"proofofwork",
    "challengeMetadata":"<json с redemptionToken>"}
   -> 200 {"challengeId":"us-central-...","challengeType":"captchav2",...}

5. POST apis.roblox.com/v2/captcha?urlLocale=en_us
   {"challenge_id":"<id второй ступени>"}
   -> 200 {"redemption_token":"<uuid>"}          <- без интерактива!

6. POST .../challenge/v1/continue
   {"challengeId":"<id второй ступени>","challengeType":"captchav2",
    "challengeMetadata":"{\"redemptionToken\":\"<uuid>\"}"}
   -> 200 {"challengeId":"...","challengeType":"","challengeMetadata":""}
      (цепочка закрыта: тип и метаданные пусты)

7. POST auth.roblox.com/v2/login + те же тело и подпись, заголовки:
   rblx-challenge-id: <id второй ступени>
   rblx-challenge-type: captchav2
   rblx-challenge-metadata: base64({"redemptionToken":"<uuid>"})
   -> 200 {"user":{...},"isBanned":false,...} + Set-Cookie .ROBLOSECURITY
```

## Замеры

| Показатель | Значение |
|---|---|
| Решение PoW | 1.2 с |
| Полный вход | 4.0–5.5 с |
| Интерактивных действий | 0 |
| Браузер | не используется |
| Успешных входов подряд | 8 аккаунтов, 8 живых сессий |

## Условия

| Условие | Почему важно |
|---|---|
| Подпись SAI в обмене | без неё платформа выдаёт капчу вместо задачи |
| Прогрев страницы входа | без него ответ приходит «проверка без идентификатора» |
| Подсказки клиента (`sec-ch-ua`, `sec-fetch-*`) | с ними ответ содержит заголовки проверки |
| Сложность `T` в пределах разумного | на сожжённых парах `T = 50 000 000`, задание не решается |

## Реализация

| Файл | Что делает |
|---|---|
| `flow/sai.py` | собирает подпись входа |
| `flow/http_challenge.py` | проводит всю цепочку и повторяет обмен |
| `flow/client.py` | вызывает цепочку до браузерного провайдера |
| `probes/no_captcha_batch.py` | пакетная проверка пути |

Проверка сервисом: `python -m tools.try_flow --account <login> --proxy-url <url>`
даёт `SUCCESS` с подтверждённой сессией за ~8 с на новом адресе.


Проверено: метаданные `{"redemptionToken": "<uuid>"}` в base64, заголовки
`rblx-challenge-id` (значение из ответа), `rblx-challenge-type` →
`403 Challenge failed to authorize request`.

Дальше: `x-retry-attempt`, `genericChallengeId` вместо `challenge-id`,
вложенность токена, и снятие точных заголовков с браузера в момент PoW
(один прогон, чтобы увидеть эталон).

## Откуда восстановлено

- `var/js/28-*.js` — сервис `genericChallenge.continueChallenge`,
  схемы типов (`proofofwork` → `{sessionId}`), маппинг обработчиков.
- `var/pow-worker.js` — воркер `ChallengeWebWorkers`, типы заданий
  `TIME_LOCK_PUZZLE` и `GRAPH_PEBBLING_PUZZLE`.
- Живые запросы: `probes/pow_full.py`, `probes/challenge_continue.py`.
