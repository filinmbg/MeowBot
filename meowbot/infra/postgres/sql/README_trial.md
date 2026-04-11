# Trial support SQL

## Порядок запуску

Якщо базова схема вже створена:

1. `alter_trial_support.sql`
2. `seed_plans_v2.sql`
3. за потреби `seed_dev_v2.sql`
4. `trial_functions.sql`

---

## Що це додає

### Нові плани
- `free`
- `trial_pro`
- `basic`
- `pro`
- `vip`

### Trial-захист
- trial only once per user
- per telegram
- per email
- per api key fingerprint
- per exchange account fingerprint

### Fallback
Після завершення `trial_pro`, якщо в користувача немає активної paid subscription:
- активується `free`
- `trading_mode` переводиться в `sandbox`

---

## API key policy

Для trial/live ключ повинен:
- мати trading permission
- не мати withdrawal permission

Рекомендовано також:
- IP whitelist
- read permission