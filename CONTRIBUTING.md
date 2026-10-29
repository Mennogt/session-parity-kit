# Участие в проекте / Contributing

## Русский

### Что приветствуется

* новые зонды с воспроизводимым замером;
* уточнение протокола (заголовки, типы проверок, форматы);
* исправления в сервисе: устойчивость, наблюдаемость, тесты;
* переводы документации.

### Правила

1. **Никаких живых секретов.** Ни в коде, ни в тестах, ни в issue, ни в
   логах. Cookie, пары «логин + пароль», адреса прокси — только локально в
   `var/`, который исключён из Git.
2. **Обезличивайте данные.** Имена аккаунтов заменяйте на `Account A`,
   `Account B`; идентификаторы и адреса — на заглушки.
3. **Каждый вывод — с замером.** В описании изменения укажите, что и как
   измерялось: код ответа, время, число попыток.
4. **Тесты не ломаем.** `python -m pytest -q` должен проходить.
5. **Один зонд — одна гипотеза.** Так проще проверять и откатывать.

### Как предложить изменение

```bash
git checkout -b feature/short-name
# правки
python -m pytest -q
git commit -m "probe: describe what was measured"
git push origin feature/short-name
```

В pull request заполните шаблон: что проверяли, какие числа получили, что
может сломаться.

---

## English

### What is welcome

* new probes with reproducible measurements;
* protocol refinements (headers, challenge types, formats);
* service fixes: resilience, observability, tests;
* documentation translations.

### Rules

1. **No live secrets.** Not in code, tests, issues or logs. Cookies,
   login/password pairs and proxy endpoints stay local in `var/`, which is
   git-ignored.
2. **Anonymise data.** Replace account names with `Account A`, `Account B`;
   replace identifiers and addresses with placeholders.
3. **Every claim needs a measurement.** State what was measured and how:
   status code, timing, number of attempts.
4. **Keep tests green.** `python -m pytest -q` must pass.
5. **One probe, one hypothesis.** Easier to review and revert.

### Proposing a change

```bash
git checkout -b feature/short-name
# edits
python -m pytest -q
git commit -m "probe: describe what was measured"
git push origin feature/short-name
```

Fill the pull request template: what you tested, which numbers you got, what
may break.
