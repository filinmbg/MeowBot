# MeowBot Postgres SQL

Ця папка містить SQL-файли для ініціалізації, скидання, наповнення і перевірки PostgreSQL-схеми MeowBot.

## Структура папки

```text
meowbot/infra/postgres/sql/
├── README.md
├── reset.sql
├── init.sql
├── seed_plans.sql
├── seed_dev.sql
└── debug_queries.sql

Опис файлів
reset.sql

Повністю видаляє:

view
таблиці
helper function set_updated_at()

Використовувати, коли треба:

повністю перезібрати схему
прибрати старі тестові дані
почати з нуля

init.sql

Створює:

всі основні таблиці
індекси
тригери updated_at
view для роботи системи

Це основний файл схеми.

seed_plans.sql

Створює та оновлює:

тарифні плани
дозволені монети для тарифів

Цей файл не створює тестового користувача.

seed_dev.sql

Створює або оновлює тестові dev-дані:

test user
auth identity
telegram profile
trader settings
notification preferences
active subscription
user symbol settings
user timeframe settings
bot session

Призначений для локального тестування і dev-середовища.

debug_queries.sql

Набір корисних SQL-запитів для ручної перевірки:

таблиці
view
користувачі
підписки
allowed symbols
enabled symbols
telegram targets
payments
webhook events
audit logs
bot sessions
Рекомендований порядок запуску
Повний запуск з нуля
reset.sql
init.sql
seed_plans.sql
seed_dev.sql
Якщо схема вже створена, але треба оновити тарифи
seed_plans.sql
Якщо треба лише тестового користувача
seed_dev.sql

Важливо: перед цим вже мають існувати таблиці і бажано тариф free.

Якщо треба перевірити стан бази
debug_queries.sql