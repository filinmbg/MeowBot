# PROJECT_CONTEXT

Автоматично згенерована чернетка контексту проєкту.
Частину розділів потрібно перевірити і доповнити вручну.

## 1. Корінь проєкту

`D:\Project\MeowBot`

## 2. Дерево проєкту

```text
├── .vscode
│   └── settings.json
├── meowbot
│   ├── apps
│   │   ├── api
│   │   │   ├── routes
│   │   │   │   ├── settings.py
│   │   │   │   ├── telegram_auth.py
│   │   │   │   └── users.py
│   │   │   ├── schemas
│   │   │   │   ├── settings.py
│   │   │   │   ├── telegram_auth.py
│   │   │   │   └── users.py
│   │   │   └── main.py
│   │   ├── bot_online
│   │   │   ├── bootstrap.py
│   │   │   ├── handlers_start.py
│   │   │   ├── main.py
│   │   │   └── runner_async.py
│   │   └── telegram_bot
│   │       ├── bootstrap.py
│   │       ├── handlers_start.py
│   │       ├── keyboards_main.py
│   │       └── main.py
│   ├── core
│   │   ├── domain
│   │   │   ├── __init__.py
│   │   │   ├── enums.py
│   │   │   └── types.py
│   │   ├── ports
│   │   │   ├── __init__.py
│   │   │   ├── account_repo.py
│   │   │   ├── bars_repo.py
│   │   │   ├── bot_state_repo.py
│   │   │   ├── broker.py
│   │   │   ├── entry_strategy.py
│   │   │   ├── exchange_market_data.py
│   │   │   ├── policy.py
│   │   │   ├── trade_events_repo.py
│   │   │   └── trades_repo.py
│   │   ├── services
│   │   │   └── execution
│   │   │       ├── entry
│   │   │       │   ├── policy_entry_strategy.py
│   │   │       │   └── portfolio_gate.py
│   │   │       ├── exit
│   │   │       │   └── tp_sl_cascade.py
│   │   │       └── risk
│   │   │           ├── limits.py
│   │   │           └── sizing.py
│   │   ├── usecases
│   │   │   ├── ensure_market_data.py
│   │   │   ├── get_or_create_user_by_telegram.py
│   │   │   ├── online_entry_cycle.py
│   │   │   ├── reconcile_trades.py
│   │   │   └── run_entry_cycle.py
│   │   └── __init__.py
│   ├── infra
│   │   ├── broker
│   │   │   └── paper.py
│   │   ├── exchange
│   │   │   ├── binance_futures_usdtm.py
│   │   │   └── binance_spot.py
│   │   ├── memory
│   │   │   ├── account_repo.py
│   │   │   ├── bars_repo.py
│   │   │   ├── broker.py
│   │   │   ├── exchange.py
│   │   │   ├── policy.py
│   │   │   └── trades_repo.py
│   │   ├── mongo
│   │   │   ├── repos
│   │   │   │   ├── __init__.py
│   │   │   │   ├── bars_repo.py
│   │   │   │   ├── bot_state_repo.py
│   │   │   │   ├── trade_events_repo.py
│   │   │   │   └── trades_repo.py
│   │   │   ├── __init__.py
│   │   │   ├── client.py
│   │   │   └── migrations.py
│   │   ├── postgres
│   │   │   ├── repos
│   │   │   │   ├── telegram_auth_repo.py
│   │   │   │   ├── user_defaults_repo.py
│   │   │   │   └── users_repo.py
│   │   │   ├── sql
│   │   │   │   ├── 001_create_users.sql
│   │   │   │   ├── 002_create_auth_and_telegram.sql
│   │   │   │   └── 003_create_trader_and_notifications.sql
│   │   │   ├── apply_sql_file.py
│   │   │   ├── check_connection.py
│   │   │   └── client.py
│   │   └── check_all_connections.py
│   ├── meowbot
│   │   └── apps
│   │       └── telegram_bot
│   │           └── handlers_menu.py
│   ├── tests
│   │   ├── test_domain_types.py
│   │   ├── test_ensure_market_data.py
│   │   ├── test_imports.py
│   │   ├── test_mongo_bars_repo.py
│   │   ├── test_mongo_smoke.py
│   │   ├── test_mongo_trades_repo.py
│   │   ├── test_online_entry_cycle.py
│   │   ├── test_paper_broker.py
│   │   ├── test_policy_entry_strategy.py
│   │   ├── test_portfolio_risk.py
│   │   ├── test_reconcile_trades.py
│   │   ├── test_run_entry_cycle.py
│   │   ├── test_tp_sl_cascade.py
│   │   ├── test_trade_events_on_exit.py
│   │   └── test_trade_events_repo.py
│   └── __init__.py
├── scripts
│   └── generate_project_context.py
├── test
│   ├── analysis
│   │   └── indicator_bucket_analysis.py
│   ├── backtest
│   │   ├── __init__.py
│   │   ├── backtest_rsi_rebound_supertrend_core.py
│   │   ├── run_backtest_rsi_rebound_supertrend_15m.py
│   │   ├── run_backtest_rsi_rebound_supertrend_1h.py
│   │   ├── run_backtest_rsi_rebound_supertrend_2h.py
│   │   ├── run_backtest_rsi_rebound_supertrend_30m.py
│   │   └── run_backtest_rsi_rebound_supertrend_4h.py
│   ├── data
│   │   ├── BTCUSDT
│   │   │   ├── BTCUSDT_15m.csv.gz
│   │   │   ├── BTCUSDT_15m_indicators.csv.gz
│   │   │   ├── BTCUSDT_1h.csv.gz
│   │   │   ├── BTCUSDT_1h_indicators.csv.gz
│   │   │   ├── BTCUSDT_1m.csv.gz
│   │   │   ├── BTCUSDT_1m_indicators.csv.gz
│   │   │   ├── BTCUSDT_2h.csv.gz
│   │   │   ├── BTCUSDT_2h_indicators.csv.gz
│   │   │   ├── BTCUSDT_30m.csv.gz
│   │   │   ├── BTCUSDT_30m_indicators.csv.gz
│   │   │   ├── BTCUSDT_4h.csv.gz
│   │   │   ├── BTCUSDT_4h_indicators.csv.gz
│   │   │   ├── BTCUSDT_5m.csv.gz
│   │   │   └── BTCUSDT_5m_indicators.csv.gz
│   │   └── download_bars.py
│   ├── indicators
│   │   ├── __init__.py
│   │   └── build_indicators.py
│   ├── results
│   │   ├── BTCUSDT_15m_rsi_rebound_supertrend_summary.csv
│   │   ├── BTCUSDT_15m_rsi_rebound_supertrend_trades.csv
│   │   ├── BTCUSDT_1h_rsi_rebound_supertrend_summary.csv
│   │   ├── BTCUSDT_1h_rsi_rebound_supertrend_trades.csv
│   │   ├── BTCUSDT_2h_rsi_rebound_supertrend_summary.csv
│   │   ├── BTCUSDT_2h_rsi_rebound_supertrend_trades.csv
│   │   ├── BTCUSDT_30m_rsi_rebound_supertrend_summary.csv
│   │   ├── BTCUSDT_30m_rsi_rebound_supertrend_trades.csv
│   │   ├── BTCUSDT_4h_rsi_rebound_supertrend_summary.csv
│   │   └── BTCUSDT_4h_rsi_rebound_supertrend_trades.csv
│   ├── scripts
│   │   ├── __init__.py
│   │   └── download_binance_bars.py
│   ├── strategies
│   │   ├── __init__.py
│   │   └── indicator_strategies.py
│   └── __init__.py
├── .env
├── .gitignore
├── cpu.py
├── LICENSE
├── main.py
├── pyproject.toml
├── README.md
└── requirements.txt
```

## 3. Верхньорівневі Python-пакети

- `meowbot`
- `test`

## 4. Важливі файли

- `.env`
- `main.py`
- `meowbot/apps/api/main.py`
- `meowbot/apps/api/routes/settings.py`
- `meowbot/apps/api/schemas/settings.py`
- `meowbot/apps/bot_online/main.py`
- `meowbot/apps/telegram_bot/main.py`
- `pyproject.toml`
- `README.md`
- `requirements.txt`

## 5. Імовірні точки входу

- `main.py` — ознаки: main-guard, asyncio
  - Можливий запуск: `python -m main`
- `meowbot/apps/api/main.py` — ознаки: uvicorn/fastapi
  - Можливий запуск: `python -m meowbot.apps.api.main`
- `meowbot/apps/bot_online/main.py` — ознаки: main-guard, asyncio
  - Можливий запуск: `python -m meowbot.apps.bot_online.main`
- `meowbot/apps/bot_online/runner_async.py` — ознаки: main-guard, asyncio
  - Можливий запуск: `python -m meowbot.apps.bot_online.runner_async`
- `meowbot/apps/telegram_bot/main.py` — ознаки: main-guard, asyncio
  - Можливий запуск: `python -m meowbot.apps.telegram_bot.main`
- `meowbot/infra/check_all_connections.py` — ознаки: main-guard, asyncio
  - Можливий запуск: `python -m meowbot.infra.check_all_connections`
- `meowbot/infra/postgres/apply_sql_file.py` — ознаки: main-guard, asyncio
  - Можливий запуск: `python -m meowbot.infra.postgres.apply_sql_file`
- `meowbot/infra/postgres/check_connection.py` — ознаки: main-guard, asyncio
  - Можливий запуск: `python -m meowbot.infra.postgres.check_connection`
- `scripts/generate_project_context.py` — ознаки: main-guard, uvicorn/fastapi, asyncio
  - Можливий запуск: `python -m scripts.generate_project_context`
- `test/analysis/indicator_bucket_analysis.py` — ознаки: main-guard
  - Можливий запуск: `python -m test.analysis.indicator_bucket_analysis`
- `test/backtest/run_backtest_rsi_rebound_supertrend_15m.py` — ознаки: main-guard
  - Можливий запуск: `python -m test.backtest.run_backtest_rsi_rebound_supertrend_15m`
- `test/backtest/run_backtest_rsi_rebound_supertrend_1h.py` — ознаки: main-guard
  - Можливий запуск: `python -m test.backtest.run_backtest_rsi_rebound_supertrend_1h`
- `test/backtest/run_backtest_rsi_rebound_supertrend_2h.py` — ознаки: main-guard
  - Можливий запуск: `python -m test.backtest.run_backtest_rsi_rebound_supertrend_2h`
- `test/backtest/run_backtest_rsi_rebound_supertrend_30m.py` — ознаки: main-guard
  - Можливий запуск: `python -m test.backtest.run_backtest_rsi_rebound_supertrend_30m`
- `test/backtest/run_backtest_rsi_rebound_supertrend_4h.py` — ознаки: main-guard
  - Можливий запуск: `python -m test.backtest.run_backtest_rsi_rebound_supertrend_4h`
- `test/data/download_bars.py` — ознаки: main-guard
  - Можливий запуск: `python -m test.data.download_bars`
- `test/indicators/build_indicators.py` — ознаки: main-guard
  - Можливий запуск: `python -m test.indicators.build_indicators`
- `test/scripts/download_binance_bars.py` — ознаки: main-guard
  - Можливий запуск: `python -m test.scripts.download_binance_bars`

## 6. Залежності

### requirements.txt

- Порожньо або не вдалося прочитати

### pyproject.toml

- Залежності не вдалося надійно витягнути автоматично

## 7. Ключові Python-файли

- `main.py`
  - Функції: incident_code, check_supabase | Async-функції: notify_admin, retry_check, check_mongo, start_pipeline, amain
- `meowbot/__init__.py`
  - Не вдалося прочитати файл.
- `meowbot/apps/api/main.py`
  - Async-функції: startup, shutdown, health
- `meowbot/apps/api/routes/settings.py`
  - Async-функції: get_trader_settings, get_notification_preferences
- `meowbot/apps/api/routes/telegram_auth.py`
  - Async-функції: get_or_create_user_by_telegram
- `meowbot/apps/api/routes/users.py`
  - Async-функції: create_user, get_user
- `meowbot/apps/api/schemas/settings.py`
  - Класи: TraderSettingsResponse, NotificationPreferencesResponse
- `meowbot/apps/api/schemas/telegram_auth.py`
  - Класи: TelegramLoginRequest, TelegramUserResponse
- `meowbot/apps/api/schemas/users.py`
  - Класи: CreateUserRequest, UserResponse
- `meowbot/apps/bot_online/bootstrap.py`
  - Функції: build_get_or_create_user_by_telegram_usecase
- `meowbot/apps/bot_online/handlers_start.py`
  - Async-функції: cmd_start
- `meowbot/apps/bot_online/main.py`
  - Async-функції: entry_job, exit_job
- `meowbot/apps/bot_online/runner_async.py`
  - Async-функції: main
- `meowbot/apps/telegram_bot/bootstrap.py`
  - Функції: build_get_or_create_user_by_telegram_usecase
- `meowbot/apps/telegram_bot/handlers_start.py`
  - Async-функції: cmd_start
- `meowbot/apps/telegram_bot/keyboards_main.py`
  - Функції: main_menu_keyboard
- `meowbot/apps/telegram_bot/main.py`
  - Async-функції: main
- `meowbot/core/__init__.py`
  - Не вдалося прочитати файл.
- `meowbot/core/domain/__init__.py`
  - Не вдалося прочитати файл.
- `meowbot/core/domain/enums.py`
  - Класи: Side, TradeStatus, SignalAction
- `meowbot/core/domain/types.py`
  - Класи: Bar, Signal, Trade
- `meowbot/core/ports/__init__.py`
  - Не вдалося прочитати файл.
- `meowbot/core/ports/account_repo.py`
  - Класи: AccountRepository
- `meowbot/core/ports/bars_repo.py`
  - Класи: BarsRepository
- `meowbot/core/ports/bot_state_repo.py`
  - Класи: BotStateRepository
- `meowbot/core/ports/broker.py`
  - Класи: Broker
- `meowbot/core/ports/entry_strategy.py`
  - Класи: EntryStrategy
- `meowbot/core/ports/exchange_market_data.py`
  - Класи: ExchangeMarketData
- `meowbot/core/ports/policy.py`
  - Класи: Policy
- `meowbot/core/ports/trade_events_repo.py`
  - Класи: TradeEventsRepository

## 8. Що потрібно дописати вручну

- Яка архітектура є фінальною: FastAPI / Telegram bot / workers / ML pipelines
- Які модулі актуальні, а які deprecated
- Які команди запуску є канонічними
- Які папки не можна ламати або перейменовувати
- Які змінні середовища обов'язкові
- Які бізнес-правила важливі для подальшої генерації коду

## 9. Нотатки

Цей файл створено автоматично. Перед використанням як єдиного джерела правди його треба перевірити.


## Канонічна роль частин системи

- Трейдинг / сигнали / виконання стратегій — головне ядро проєкту
- ML / backtest / indicators / datasets — контур підтримки трейдингового ядра
- Telegram bot — користувацький інтерфейс для керування, перегляду стану, команд
- FastAPI — інтеграційний та сервісний шар
- Postgres / Mongo / Supabase — інфраструктурні залежності


## 10. Канонічна архітектура
- Головна мета проєкту: алгоритмічний трейдинг
- Основне ядро: трейдинг-логіка, сигнали, ризик-менеджмент, виконання
- Telegram bot: допоміжний інтерфейс для керування та моніторингу
- FastAPI: службовий API та інтеграції
- ML/backtest/test-data: окремий дослідницький та тренувальний контур

## 11. Правило генерації коду
- Не переносити бізнес-логіку в Telegram handlers
- Не переносити бізнес-логіку в FastAPI routes
- Routes і handlers повинні бути тонкими
- Основна логіка має жити в service/usecase/domain-рівні
- Доступ до БД і зовнішніх сервісів — через infra
