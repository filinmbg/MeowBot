-- meowbot_postgres_init.sql
-- PostgreSQL / Supabase
-- Базова схема для MeowBot

BEGIN;

-- --------------------------------------------------
-- Extensions
-- --------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- --------------------------------------------------
-- Updated at helper
-- --------------------------------------------------
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- --------------------------------------------------
-- users
-- Центральна сутність системи
-- --------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'disabled', 'deleted')),

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);
CREATE INDEX IF NOT EXISTS idx_users_is_active ON users(is_active);

DROP TRIGGER IF EXISTS trg_users_updated_at ON users;
CREATE TRIGGER trg_users_updated_at
BEFORE UPDATE ON users
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- --------------------------------------------------
-- auth_identities
-- Способи ідентифікації: telegram / email / google ...
-- --------------------------------------------------
CREATE TABLE IF NOT EXISTS auth_identities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    provider TEXT NOT NULL
        CHECK (provider IN ('telegram', 'email', 'google', 'apple', 'other')),

    provider_user_id TEXT NOT NULL,
    provider_email TEXT NULL,
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    is_verified BOOLEAN NOT NULL DEFAULT FALSE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (provider, provider_user_id)
);

CREATE INDEX IF NOT EXISTS idx_auth_identities_user_id ON auth_identities(user_id);
CREATE INDEX IF NOT EXISTS idx_auth_identities_provider ON auth_identities(provider);
CREATE INDEX IF NOT EXISTS idx_auth_identities_provider_email ON auth_identities(provider_email);

DROP TRIGGER IF EXISTS trg_auth_identities_updated_at ON auth_identities;
CREATE TRIGGER trg_auth_identities_updated_at
BEFORE UPDATE ON auth_identities
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- --------------------------------------------------
-- telegram_profiles
-- Telegram-specific профіль користувача
-- --------------------------------------------------
CREATE TABLE IF NOT EXISTS telegram_profiles (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,

    telegram_id BIGINT NOT NULL UNIQUE,
    username TEXT NULL,
    first_name TEXT NULL,
    last_name TEXT NULL,
    language_code TEXT NULL,
    is_bot BOOLEAN NOT NULL DEFAULT FALSE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_telegram_profiles_telegram_id ON telegram_profiles(telegram_id);
CREATE INDEX IF NOT EXISTS idx_telegram_profiles_username ON telegram_profiles(username);

DROP TRIGGER IF EXISTS trg_telegram_profiles_updated_at ON telegram_profiles;
CREATE TRIGGER trg_telegram_profiles_updated_at
BEFORE UPDATE ON telegram_profiles
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- --------------------------------------------------
-- trader_settings
-- Загальні торгові налаштування користувача
-- --------------------------------------------------
CREATE TABLE IF NOT EXISTS trader_settings (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,

    trading_enabled BOOLEAN NOT NULL DEFAULT FALSE,

    trading_mode TEXT NOT NULL DEFAULT 'sandbox'
        CHECK (trading_mode IN ('sandbox', 'real')),

    leverage INTEGER NOT NULL DEFAULT 20
        CHECK (leverage >= 1 AND leverage <= 125),

    risk_per_trade_pct NUMERIC(8,4) NOT NULL DEFAULT 1.0000
        CHECK (risk_per_trade_pct > 0 AND risk_per_trade_pct <= 100),

    max_open_trades INTEGER NOT NULL DEFAULT 1
        CHECK (max_open_trades >= 1 AND max_open_trades <= 1000),

    max_open_trades_per_symbol INTEGER NOT NULL DEFAULT 1
        CHECK (max_open_trades_per_symbol >= 1 AND max_open_trades_per_symbol <= 100),

    default_order_size_usdt NUMERIC(18,8) NULL
        CHECK (default_order_size_usdt IS NULL OR default_order_size_usdt > 0),

    capital_allocation_mode TEXT NOT NULL DEFAULT 'risk_percent'
        CHECK (capital_allocation_mode IN ('risk_percent', 'fixed_usdt')),

    exchange_name TEXT NOT NULL DEFAULT 'binance'
        CHECK (exchange_name IN ('binance')),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DROP TRIGGER IF EXISTS trg_trader_settings_updated_at ON trader_settings;
CREATE TRIGGER trg_trader_settings_updated_at
BEFORE UPDATE ON trader_settings
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- --------------------------------------------------
-- trader_symbol_settings
-- Налаштування символів/монет
-- --------------------------------------------------
CREATE TABLE IF NOT EXISTS trader_symbol_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (user_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_trader_symbol_settings_user_id ON trader_symbol_settings(user_id);
CREATE INDEX IF NOT EXISTS idx_trader_symbol_settings_symbol ON trader_symbol_settings(symbol);
CREATE INDEX IF NOT EXISTS idx_trader_symbol_settings_user_enabled
    ON trader_symbol_settings(user_id, is_enabled);

DROP TRIGGER IF EXISTS trg_trader_symbol_settings_updated_at ON trader_symbol_settings;
CREATE TRIGGER trg_trader_symbol_settings_updated_at
BEFORE UPDATE ON trader_symbol_settings
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- --------------------------------------------------
-- trader_timeframe_settings
-- Налаштування таймфреймів
-- --------------------------------------------------
CREATE TABLE IF NOT EXISTS trader_timeframe_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    timeframe TEXT NOT NULL
        CHECK (timeframe IN ('15m', '30m', '1h', '4h', '1d')),

    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (user_id, timeframe)
);

CREATE INDEX IF NOT EXISTS idx_trader_timeframe_settings_user_id
    ON trader_timeframe_settings(user_id);

CREATE INDEX IF NOT EXISTS idx_trader_timeframe_settings_user_enabled
    ON trader_timeframe_settings(user_id, is_enabled);

DROP TRIGGER IF EXISTS trg_trader_timeframe_settings_updated_at ON trader_timeframe_settings;
CREATE TRIGGER trg_trader_timeframe_settings_updated_at
BEFORE UPDATE ON trader_timeframe_settings
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- --------------------------------------------------
-- notification_preferences
-- Telegram / system notification settings
-- --------------------------------------------------
CREATE TABLE IF NOT EXISTS notification_preferences (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,

    language TEXT NOT NULL DEFAULT 'uk'
        CHECK (language IN ('uk', 'en', 'ru')),

    notify_trade_open BOOLEAN NOT NULL DEFAULT TRUE,
    notify_trade_tp BOOLEAN NOT NULL DEFAULT TRUE,
    notify_trade_sl BOOLEAN NOT NULL DEFAULT TRUE,
    notify_trade_close BOOLEAN NOT NULL DEFAULT TRUE,

    notify_system BOOLEAN NOT NULL DEFAULT TRUE,
    notify_subscription BOOLEAN NOT NULL DEFAULT TRUE,

    quiet_hours_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    quiet_hours_start TIME NULL,
    quiet_hours_end TIME NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DROP TRIGGER IF EXISTS trg_notification_preferences_updated_at ON notification_preferences;
CREATE TRIGGER trg_notification_preferences_updated_at
BEFORE UPDATE ON notification_preferences
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- --------------------------------------------------
-- subscription_plans
-- Тарифні плани
-- --------------------------------------------------
CREATE TABLE IF NOT EXISTS subscription_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NULL,

    price_usd NUMERIC(18,2) NOT NULL DEFAULT 0.00
        CHECK (price_usd >= 0),

    duration_days INTEGER NOT NULL
        CHECK (duration_days >= 1),

    max_symbols INTEGER NOT NULL
        CHECK (max_symbols >= 1),

    max_open_trades INTEGER NOT NULL
        CHECK (max_open_trades >= 1),

    max_open_trades_per_symbol INTEGER NOT NULL DEFAULT 1
        CHECK (max_open_trades_per_symbol >= 1),

    sandbox_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    real_trading_enabled BOOLEAN NOT NULL DEFAULT FALSE,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order INTEGER NOT NULL DEFAULT 0,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_subscription_plans_is_active ON subscription_plans(is_active);
CREATE INDEX IF NOT EXISTS idx_subscription_plans_sort_order ON subscription_plans(sort_order);

DROP TRIGGER IF EXISTS trg_subscription_plans_updated_at ON subscription_plans;
CREATE TRIGGER trg_subscription_plans_updated_at
BEFORE UPDATE ON subscription_plans
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- --------------------------------------------------
-- user_subscriptions
-- Підписки користувачів
-- --------------------------------------------------
CREATE TABLE IF NOT EXISTS user_subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan_id UUID NOT NULL REFERENCES subscription_plans(id) ON DELETE RESTRICT,

    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'active', 'expired', 'cancelled', 'failed')),

    started_at TIMESTAMPTZ NULL,
    expires_at TIMESTAMPTZ NULL,
    cancelled_at TIMESTAMPTZ NULL,

    auto_renew BOOLEAN NOT NULL DEFAULT FALSE,

    source TEXT NOT NULL DEFAULT 'manual'
        CHECK (source IN ('manual', 'telegram', 'site', 'admin', 'payment_webhook')),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_subscriptions_user_id ON user_subscriptions(user_id);
CREATE INDEX IF NOT EXISTS idx_user_subscriptions_plan_id ON user_subscriptions(plan_id);
CREATE INDEX IF NOT EXISTS idx_user_subscriptions_status ON user_subscriptions(status);
CREATE INDEX IF NOT EXISTS idx_user_subscriptions_user_status
    ON user_subscriptions(user_id, status);
CREATE INDEX IF NOT EXISTS idx_user_subscriptions_expires_at ON user_subscriptions(expires_at);

DROP TRIGGER IF EXISTS trg_user_subscriptions_updated_at ON user_subscriptions;
CREATE TRIGGER trg_user_subscriptions_updated_at
BEFORE UPDATE ON user_subscriptions
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- --------------------------------------------------
-- payments
-- Платежі
-- --------------------------------------------------
CREATE TABLE IF NOT EXISTS payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subscription_id UUID NULL REFERENCES user_subscriptions(id) ON DELETE SET NULL,

    provider TEXT NOT NULL
        CHECK (provider IN ('crypto', 'stripe', 'liqpay', 'wayforpay', 'manual', 'other')),

    provider_payment_id TEXT NULL,
    external_invoice_id TEXT NULL,

    amount NUMERIC(18,8) NOT NULL
        CHECK (amount >= 0),

    currency TEXT NOT NULL,

    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'success', 'failed', 'cancelled', 'refunded')),

    payment_method TEXT NULL,
    paid_at TIMESTAMPTZ NULL,
    raw_payload JSONB NULL,
    comment TEXT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id);
CREATE INDEX IF NOT EXISTS idx_payments_subscription_id ON payments(subscription_id);
CREATE INDEX IF NOT EXISTS idx_payments_provider ON payments(provider);
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);
CREATE INDEX IF NOT EXISTS idx_payments_provider_payment_id ON payments(provider_payment_id);
CREATE INDEX IF NOT EXISTS idx_payments_created_at ON payments(created_at);

DROP TRIGGER IF EXISTS trg_payments_updated_at ON payments;
CREATE TRIGGER trg_payments_updated_at
BEFORE UPDATE ON payments
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- --------------------------------------------------
-- onboarding_state
-- Стан проходження онбордингу
-- --------------------------------------------------
CREATE TABLE IF NOT EXISTS onboarding_state (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,

    current_step TEXT NOT NULL DEFAULT 'start',
    is_completed BOOLEAN NOT NULL DEFAULT FALSE,
    completed_at TIMESTAMPTZ NULL,

    payload JSONB NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DROP TRIGGER IF EXISTS trg_onboarding_state_updated_at ON onboarding_state;
CREATE TRIGGER trg_onboarding_state_updated_at
BEFORE UPDATE ON onboarding_state
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- --------------------------------------------------
-- Корисні view
-- --------------------------------------------------

-- Активна підписка користувача
CREATE OR REPLACE VIEW v_active_user_subscriptions AS
SELECT
    us.id,
    us.user_id,
    us.plan_id,
    us.status,
    us.started_at,
    us.expires_at,
    sp.code AS plan_code,
    sp.name AS plan_name,
    sp.max_symbols,
    sp.max_open_trades,
    sp.max_open_trades_per_symbol,
    sp.sandbox_enabled,
    sp.real_trading_enabled
FROM user_subscriptions us
JOIN subscription_plans sp ON sp.id = us.plan_id
WHERE
    us.status = 'active'
    AND (us.expires_at IS NULL OR us.expires_at > NOW());

-- Користувачі, яким дозволена торгівля
CREATE OR REPLACE VIEW v_enabled_trading_users AS
SELECT
    u.id AS user_id,
    ts.trading_mode,
    ts.trading_enabled,
    ts.leverage,
    ts.risk_per_trade_pct,
    ts.max_open_trades,
    ts.max_open_trades_per_symbol,
    aus.plan_code,
    aus.plan_name,
    aus.max_symbols,
    aus.real_trading_enabled,
    tp.telegram_id,
    np.language
FROM users u
JOIN trader_settings ts
    ON ts.user_id = u.id
LEFT JOIN telegram_profiles tp
    ON tp.user_id = u.id
LEFT JOIN notification_preferences np
    ON np.user_id = u.id
JOIN v_active_user_subscriptions aus
    ON aus.user_id = u.id
WHERE
    u.is_active = TRUE
    AND u.status = 'active'
    AND ts.trading_enabled = TRUE
    AND (
        ts.trading_mode = 'sandbox'
        OR (ts.trading_mode = 'real' AND aus.real_trading_enabled = TRUE)
    );

COMMIT;