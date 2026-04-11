# Postgres schema notes for MeowBot

Цей файл пояснює людською мовою, навіщо потрібна кожна таблиця в PostgreSQL, як вони пов’язані між собою і яку роль відіграють у MeowBot.

---

# 1. Загальна ідея

У MeowBot PostgreSQL використовується для **основного state системи**:

- користувачі
- способи входу
- Telegram-профілі
- налаштування трейдингу
- підписки
- платежі
- user-facing preferences
- службові таблиці для UI / billing / admin

MongoDB при цьому лишається для **операційної частини**, де багато подій і високочастотних записів:

- bars
- features_cache
- trades
- trade_events
- bot_state
- runtime_logs
- telegram_delivery_logs

---

# 2. Центральна сутність: `users`

## Призначення
Таблиця `users` — це центр усієї системи.

Все інше прив’язується саме до `users.id`.

## Що зберігає
- email
- display name
- роль
- статус
- preferred language
- часові мітки створення та оновлення

## Навіщо потрібна
Вона дає один стабільний внутрішній `user_id`, який не залежить від Telegram, email чи інших способів входу.

Це важливо, бо:
- Telegram може бути лише одним із каналів входу
- у майбутньому може бути сайт
- може додатися Google login, email login, Apple login тощо

## Головні поля
- `id` — головний user id
- `email` — optional email
- `display_name` — ім’я користувача
- `role` — `user` або `admin`
- `status` — `active`, `blocked`, `deleted`
- `preferred_language` — `uk`, `en`, `ru`

---

# 3. `auth_identities`

## Призначення
Таблиця `auth_identities` відповідає за способи ідентифікації користувача.

## Ідея
Один користувач може мати кілька identity-джерел:
- Telegram
- email
- Google
- Apple

## Навіщо потрібна
Щоб не прив’язувати логіку системи напряму до Telegram.

Наприклад:
- один user має Telegram identity
- той самий user потім додає email login
- усе одно це один і той самий `users.id`

## Головні поля
- `user_id` — власник identity
- `provider` — `telegram`, `email`, `google`, `apple`, `other`
- `provider_user_id` — id користувача у зовнішній системі
- `provider_email` — email, якщо є
- `is_primary` — основний спосіб входу
- `is_verified` — чи верифікований спосіб входу

---

# 4. `telegram_profiles`

## Призначення
Таблиця `telegram_profiles` містить **Telegram-специфічні дані** користувача.

## Чому окрема таблиця
Тому що не всі користувачі в майбутньому зобов’язані мати Telegram.  
Telegram — це не центр системи, а один із фронтів.

## Що зберігається
- `telegram_id`
- `username`
- `first_name`
- `last_name`
- `chat_id`
- `is_onboarded`
- `last_seen_at`

## Навіщо потрібен `chat_id`
Саме в `chat_id` часто потрібно надсилати повідомлення.  
Для Telegram-бота це практично ключове поле маршрутизації.

## Навіщо `is_onboarded`
Щоб розуміти, чи користувач уже пройшов стартовий сценарій:
- вибір мови
- вибір режиму
- перші налаштування

## Навіщо `last_seen_at`
Корисно для:
- діагностики
- аналітики
- перевірки активності користувача

---

# 5. `trader_settings`

## Призначення
Головна таблиця **торгових налаштувань користувача**.

У кожного користувача один основний набір налаштувань торгівлі.

## Що зберігається
- чи ввімкнена торгівля
- sandbox або live
- режим ставки
- значення ставки
- плече
- максимум відкритих угод
- дозволи на long / short

## Основна логіка
Це глобальні налаштування для користувача.

Наприклад:
- користувач торгує тільки в `sandbox`
- хоче `1%` на угоду
- з плечем `20`
- не більше `1` угоди на монету
- long дозволені, short дозволені

## Головні поля
- `trading_enabled`
- `trading_mode` — `sandbox` або `live`
- `default_stake_mode` — `percent` або `fixed`
- `default_stake_value`
- `default_leverage`
- `max_open_trades_total`
- `max_open_trades_per_symbol`
- `allow_long`
- `allow_short`

---

# 6. `trader_symbol_settings`

## Призначення
Зберігає, які саме монети користувач собі ввімкнув.

## Важливий нюанс
Ця таблиця не повинна самостійно визначати, що користувачу **дозволено тарифом**.  
Вона визначає, що він **обрав** в межах дозволеного.

## Як це працює разом з тарифами
- `subscription_plan_symbols` визначає, які монети доступні за планом
- `trader_symbol_settings` визначає, які з них користувач реально ввімкнув

## Приклад
План дозволяє 10 монет:
- BTCUSDT
- ETHUSDT
- SOLUSDT
- ...

Користувач увімкнув тільки:
- BTCUSDT
- ETHUSDT

Оці 2 і будуть тут.

## Поля
- `user_id`
- `symbol`
- `enabled`

---

# 7. `trader_timeframe_settings`

## Призначення
Зберігає дозволені/увімкнені таймфрейми для користувача.

## Навіщо окрема таблиця
Бо користувач може захотіти:
- тільки `1h` і `4h`
- або всі ТФ
- або вимкнути `15m`

## Приклад
У користувача можуть бути такі записи:
- `15m = false`
- `30m = true`
- `1h = true`
- `4h = true`
- `1d = true`

---

# 8. `notification_preferences`

## Призначення
Таблиця керує тим, **які повідомлення отримує користувач**.

## Що тут зберігається
- загальний toggle повідомлень
- окремо OPEN / TP / CLOSE / STOP
- системні повідомлення
- quiet hours

## Навіщо це потрібно
Щоб не шити notification-логіку прямо в Telegram-код.

Бекенд має мати можливість відповісти:
- чи можна слати OPEN?
- чи можна слати STOP?
- чи ввімкнені повідомлення взагалі?
- чи зараз quiet hours?

## Приклади
Користувач може:
- вимкнути TP-нотифікації
- лишити тільки OPEN і CLOSE
- вимкнути все на ніч

---

# 9. `subscription_plans`

## Призначення
Таблиця описує **самі тарифні плани**, які ти продаєш.

Це не підписка конкретного юзера.  
Це “каталог тарифів”.

## Типові записи
- `free`
- `basic`
- `pro`

## Основні поля
- `code` — технічний код плану
- `name` — назва
- `price_usd` — ціна
- `duration_days` — тривалість
- `features_json` — можливості тарифу
- `is_active` — чи тариф активний
- `sort_order` — порядок показу

## Навіщо `features_json`
Щоб зберігати гнучкі властивості тарифу, наприклад:
- max symbols
- max trades
- live enabled
- sandbox enabled

## Приклад
```json
{
  "max_symbols": 20,
  "max_open_trades_total": 5,
  "max_open_trades_per_symbol": 1,
  "sandbox_enabled": true,
  "live_enabled": true
}


10. subscription_plan_symbols
Призначення

Ця таблиця визначає, які монети доступні для конкретного тарифного плану.

Чому вона важлива

Ти сам хотів логіку “монети спочатку залежать від підписки”.

Саме це вона і робить.

Як працює

Наприклад:

free має 10 монет
basic має 20 монет
pro має 100 монет

Тут це і зберігається.

Разом із trader_symbol_settings
subscription_plan_symbols = що дозволено тарифом
trader_symbol_settings = що користувач реально увімкнув
11. user_subscriptions
Призначення

Це вже реальні підписки конкретних користувачів.

Важливо

subscription_plans — це шаблони тарифів.
user_subscriptions — це історія та поточний стан підписок користувачів.

Що зберігається
хто купив
який план
коли почалося
коли закінчується
статус
auto renew
джерело створення
Статуси
pending
active
expired
cancelled
failed
Навіщо історія

Щоб бачити:

що користувач мав раніше
коли продовжив
коли закінчилась підписка
що було скасовано
12. payments
Призначення

Історія платежів.

Що зберігається
хто платив
за що платив
який провайдер
сума
валюта
статус
коли оплачено
raw payload від провайдера
Навіщо потрібна

Щоб мати:

фінансову історію
прив’язку до підписки
дебаг оплати
основу для білінгу та адмінки
Типові статуси
pending
processing
success
failed
cancelled
refunded
13. bot_sessions
Призначення

Це таблиця стану діалогу / flow користувача в боті або UI.

Простими словами

Вона зберігає, на якому кроці сценарію зараз знаходиться користувач.

Навіщо це потрібно

У боті часто є багатокрокові сценарії:

onboarding
підключення біржі
налаштування ризику
оформлення підписки

Потрібно десь пам’ятати:

що вже відбулося
що бот чекає далі
які проміжні дані вже є
Приклади
flow_name = onboarding, step_name = choose_language
flow_name = connect_exchange, step_name = waiting_api_key
flow_name = payment, step_name = waiting_confirmation
Навіщо state_json

Там можна тримати дрібний тимчасовий стан:

яку біржу вибрали
яку мову вибрали
який message_id пов’язаний із поточним кроком
14. user_api_keys
Призначення

Зберігає API-ключі користувача для біржі.

Дуже важливо

Тут мають зберігатися не plaintext ключі, а зашифровані значення.

Що саме зберігаємо
encrypted_api_key
encrypted_api_secret
encrypted_passphrase
Як це має працювати
користувач вводить ключі
бекенд шифрує їх
у БД записується шифротекст
при роботі з біржею бекенд розшифровує їх у пам’яті
Навіщо label

Щоб користувач або адмін бачив назву запису:

main binance
sandbox binance
futures account
Навіщо last_validated_at

Щоб знати, коли ключі востаннє перевірялися як валідні.

15. billing_webhook_events
Призначення

Зберігає webhook-події від платіжних систем.

Навіщо потрібна

Головна причина — не обробити один і той самий webhook двічі.

Простими словами

Якщо провайдер оплати кілька разів надіслав одну й ту саму подію, система має зрозуміти:

це вже було
повторно обробляти не треба
Для чого ще корисна
зберігає сирий payload
допомагає дебажити
дає історію callback-ів
дозволяє робити retry без хаосу
Основна логіка

Унікальність будується по:

provider
event_id

Якщо така подія вже є, повторно не обробляємо.

16. admin_audit_logs
Призначення

Журнал важливих ручних або адміністративних дій.

Це не runtime logs

Це не технічні логи запитів чи воркерів.
Це журнал змін у бізнес-сутностях.

Для чого потрібна

Щоб потім можна було відповісти:

хто заблокував користувача
хто активував підписку вручну
хто змінив роль
хто виправив платіж
хто видалив API-ключі
Основні поля
actor_user_id — хто зробив дію
target_user_id — до кого відноситься дія
action — що зроблено
entity_type — тип сутності
entity_id — id сутності
details_json — деталі змін
Приклади action
user_blocked
user_unblocked
subscription_activated
subscription_cancelled
role_changed
payment_marked_success
api_keys_deleted
17. Основні view
v_active_user_subscriptions

Показує тільки активні підписки.

Корисно, коли потрібно швидко отримати:

який у користувача зараз активний план
які features діють зараз
v_enabled_trading_users

Показує користувачів, яким можна торгувати.

Враховує:

user активний
trading_enabled = true
є активна підписка
якщо live, то тариф дозволяє live trading

Це дуже зручно для runtime, щоб не збирати цей join щоразу вручну.

v_user_telegram_targets

Готові Telegram-цілі для відправки повідомлень.

Корисно для notification worker:

user id
chat id
мова
notification settings
18. Як таблиці пов’язані між собою
Базовий ланцюжок

users
→ auth_identities
→ telegram_profiles
→ trader_settings
→ notification_preferences

Підписки

subscription_plans
→ subscription_plan_symbols

users
→ user_subscriptions
→ subscription_plans

Платежі

users
→ payments
→ user_subscriptions

Flow / UI

users
→ bot_sessions

Біржа

users
→ user_api_keys

Адмінські дії

users
→ admin_audit_logs

19. Що не зберігаємо тут

У цьому PostgreSQL-шарі ми свідомо не тримаємо runtime trades як основну бойову модель.

Не сюди:

bars
features cache
runtime trade events
exchange stream events
debug/runtime logs

Це краще тримати в Mongo, бо там event-driven і високочастотна робота.

20. Що можна додати пізніше

Не обов’язково зараз, але може знадобитись далі:

aggregated_stats

Для вже порахованої статистики користувача:

pnl
winrate
wins/losses
best/worst trade
balances

Для збереження user-facing balance snapshot:

sandbox balance
live balance
available margin
exchange_accounts

Якщо один користувач матиме кілька окремих біржових акаунтів.

feature_flags

Для ввімкнення/вимкнення функцій без деплою.

user_preferences

Для додаткових UI/preferences, якщо їх стане багато.

21. Практичний висновок

Поточна схема розрахована на те, щоб:

мати один стабільний user_id
не залежати архітектурно тільки від Telegram
окремо тримати торгові налаштування
окремо тримати підписки і платежі
мати базу для live trading
мати базу для адмінки і білінгу
не змішувати runtime trading data з account state

Саме така структура добре підходить для MeowBot як для системи, а не просто Telegram-бота.