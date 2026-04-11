BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- --------------------------------------------------
-- users
-- --------------------------------------------------
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    email TEXT NULL,
    display_name TEXT NULL,

    role TEXT NOT NULL DEFAULT 'user'
        CHECK (role IN ('user', 'admin')),

    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'blocked', 'deleted')),

    preferred_language TEXT NOT NULL DEFAULT 'uk'
        CHECK (preferred_language IN ('uk', 'en', 'ru')),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_users_email_unique
    ON users(email)
    WHERE email IS NOT NULL;

CREATE INDEX idx_users_status ON users(status);
CREATE INDEX idx_users_role ON users(role);
CREATE INDEX idx_users_preferred_language ON users(preferred_language);

CREATE TRIGGER trg_users_updated_at
BEFORE UPDATE ON users
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- --------------------------------------------------
-- auth_identities
-- --------------------------------------------------
CREATE TABLE auth_identities (
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

    CONSTRAINT uq_auth_identities_provider_user UNIQUE (provider, provider_user_id)
);

CREATE INDEX idx_auth_identities_user_id ON auth_identities(user_id);
CREATE INDEX idx_auth_identities_provider ON auth_identities(provider);
CREATE INDEX idx_auth_identities_provider_email ON auth_identities(provider_email);

CREATE TRIGGER trg_auth_identities_updated_at
BEFORE UPDATE ON auth_identities
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- --------------------------------------------------
-- telegram_profiles
-- --------------------------------------------------
CREATE TABLE telegram_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    user_id UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,

    telegram_id BIGINT NOT NULL UNIQUE,
    username TEXT NULL,
    first_name TEXT NULL,
    last_name TEXT NULL,
    chat_id BIGINT NOT NULL,

    is_onboarded BOOLEAN NOT NULL DEFAULT FALSE,
    last_seen_at TIMESTAMPTZ NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_telegram_profiles_user_id ON telegram_profiles(user_id);
CREATE INDEX idx_telegram_profiles_chat_id ON telegram_profiles(chat_id);
CREATE INDEX idx_telegram_profiles_username ON telegram_profiles(username);
CREATE INDEX idx_telegram_profiles_is_onboarded ON telegram_profiles(is_onboarded);

CREATE TRIGGER trg_telegram_profiles_updated_at
BEFORE UPDATE ON telegram_profiles
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- --------------------------------------------------
-- trader_settings
-- --------------------------------------------------
CREATE TABLE trader_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    user_id UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,

    trading_enabled BOOLEAN NOT NULL DEFAULT FALSE,

    trading_mode TEXT NOT NULL DEFAULT 'sandbox'
        CHECK (trading_mode IN ('sandbox', 'live')),

    default_stake_mode TEXT NOT NULL DEFAULT 'percent'
        CHECK (default_stake_mode IN ('percent', 'fixed')),

    default_stake_value NUMERIC(18,8) NOT NULL DEFAULT 1.0
        CHECK (default_stake_value > 0),

    default_leverage INTEGER NOT NULL DEFAULT 20
        CHECK (default_leverage >= 1 AND default_leverage <= 125),

    max_open_trades_total INTEGER NOT NULL DEFAULT 1
        CHECK (max_open_trades_total >= 1),

    max_open_trades_per_symbol INTEGER NOT NULL DEFAULT 1
        CHECK (max_open_trades_per_symbol >= 1),

    allow_long BOOLEAN NOT NULL DEFAULT TRUE,
    allow_short BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_trader_settings_user_id ON trader_settings(user_id);
CREATE INDEX idx_trader_settings_trading_enabled ON trader_settings(trading_enabled);
CREATE INDEX idx_trader_settings_trading_mode ON trader_settings(trading_mode);

CREATE TRIGGER trg_trader_settings_updated_at
BEFORE UPDATE ON trader_settings
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- --------------------------------------------------
-- trader_symbol_settings
-- --------------------------------------------------
CREATE TABLE trader_symbol_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_trader_symbol_settings_user_symbol UNIQUE (user_id, symbol)
);

CREATE INDEX idx_trader_symbol_settings_user_id ON trader_symbol_settings(user_id);
CREATE INDEX idx_trader_symbol_settings_symbol ON trader_symbol_settings(symbol);
CREATE INDEX idx_trader_symbol_settings_user_enabled
    ON trader_symbol_settings(user_id, enabled);

CREATE TRIGGER trg_trader_symbol_settings_updated_at
BEFORE UPDATE ON trader_symbol_settings
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- --------------------------------------------------
-- trader_timeframe_settings
-- --------------------------------------------------
CREATE TABLE trader_timeframe_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    timeframe TEXT NOT NULL
        CHECK (timeframe IN ('15m', '30m', '1h', '4h', '1d')),

    enabled BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_trader_timeframe_settings_user_timeframe UNIQUE (user_id, timeframe)
);

CREATE INDEX idx_trader_timeframe_settings_user_id ON trader_timeframe_settings(user_id);
CREATE INDEX idx_trader_timeframe_settings_user_enabled
    ON trader_timeframe_settings(user_id, enabled);

CREATE TRIGGER trg_trader_timeframe_settings_updated_at
BEFORE UPDATE ON trader_timeframe_settings
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- --------------------------------------------------
-- notification_preferences
-- --------------------------------------------------
CREATE TABLE notification_preferences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    user_id UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,

    notifications_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    notify_trade_opened BOOLEAN NOT NULL DEFAULT TRUE,
    notify_tp_hit BOOLEAN NOT NULL DEFAULT TRUE,
    notify_trade_closed BOOLEAN NOT NULL DEFAULT TRUE,
    notify_stop_loss BOOLEAN NOT NULL DEFAULT TRUE,
    notify_system BOOLEAN NOT NULL DEFAULT TRUE,

    quiet_hours_from TIME NULL,
    quiet_hours_to TIME NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_notification_preferences_user_id ON notification_preferences(user_id);
CREATE INDEX idx_notification_preferences_enabled
    ON notification_preferences(notifications_enabled);

CREATE TRIGGER trg_notification_preferences_updated_at
BEFORE UPDATE ON notification_preferences
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- --------------------------------------------------
-- subscription_plans
-- --------------------------------------------------
CREATE TABLE subscription_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,

    price_usd NUMERIC(18,2) NOT NULL DEFAULT 0
        CHECK (price_usd >= 0),

    duration_days INTEGER NOT NULL
        CHECK (duration_days >= 1),

    features_json JSONB NOT NULL DEFAULT '{}'::jsonb,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order INTEGER NOT NULL DEFAULT 0,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_subscription_plans_is_active ON subscription_plans(is_active);
CREATE INDEX idx_subscription_plans_sort_order ON subscription_plans(sort_order);

CREATE TRIGGER trg_subscription_plans_updated_at
BEFORE UPDATE ON subscription_plans
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- --------------------------------------------------
-- subscription_plan_symbols
-- --------------------------------------------------
CREATE TABLE subscription_plan_symbols (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    plan_id UUID NOT NULL REFERENCES subscription_plans(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_subscription_plan_symbols_plan_symbol UNIQUE (plan_id, symbol)
);

CREATE INDEX idx_subscription_plan_symbols_plan_id ON subscription_plan_symbols(plan_id);
CREATE INDEX idx_subscription_plan_symbols_symbol ON subscription_plan_symbols(symbol);

-- --------------------------------------------------
-- user_subscriptions
-- --------------------------------------------------
CREATE TABLE user_subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan_id UUID NOT NULL REFERENCES subscription_plans(id) ON DELETE RESTRICT,

    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'active', 'expired', 'cancelled', 'failed')),

    starts_at TIMESTAMPTZ NULL,
    ends_at TIMESTAMPTZ NULL,
    auto_renew BOOLEAN NOT NULL DEFAULT FALSE,
    cancelled_at TIMESTAMPTZ NULL,

    source TEXT NOT NULL DEFAULT 'manual'
        CHECK (source IN ('manual', 'telegram', 'site', 'admin', 'payment_webhook')),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_user_subscriptions_user_id ON user_subscriptions(user_id);
CREATE INDEX idx_user_subscriptions_plan_id ON user_subscriptions(plan_id);
CREATE INDEX idx_user_subscriptions_status ON user_subscriptions(status);
CREATE INDEX idx_user_subscriptions_ends_at ON user_subscriptions(ends_at);
CREATE INDEX idx_user_subscriptions_user_status
    ON user_subscriptions(user_id, status);

CREATE TRIGGER trg_user_subscriptions_updated_at
BEFORE UPDATE ON user_subscriptions
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- --------------------------------------------------
-- payments
-- --------------------------------------------------
CREATE TABLE payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subscription_id UUID NULL REFERENCES user_subscriptions(id) ON DELETE SET NULL,

    provider TEXT NOT NULL,
    provider_payment_id TEXT NULL,

    currency TEXT NOT NULL,
    amount NUMERIC(18,8) NOT NULL
        CHECK (amount >= 0),

    status TEXT NOT NULL
        CHECK (status IN ('pending', 'processing', 'success', 'failed', 'cancelled', 'refunded')),

    paid_at TIMESTAMPTZ NULL,
    raw_payload JSONB NULL,
    comment TEXT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_payments_user_id ON payments(user_id);
CREATE INDEX idx_payments_subscription_id ON payments(subscription_id);
CREATE INDEX idx_payments_status ON payments(status);
CREATE INDEX idx_payments_provider_payment_id ON payments(provider_payment_id);
CREATE INDEX idx_payments_provider ON payments(provider);

CREATE TRIGGER trg_payments_updated_at
BEFORE UPDATE ON payments
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- --------------------------------------------------
-- bot_sessions
-- --------------------------------------------------
CREATE TABLE bot_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    flow_name TEXT NOT NULL,
    step_name TEXT NOT NULL,
    state_json JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_bot_sessions_user_id ON bot_sessions(user_id);
CREATE INDEX idx_bot_sessions_flow_name ON bot_sessions(flow_name);
CREATE INDEX idx_bot_sessions_user_flow ON bot_sessions(user_id, flow_name);

CREATE TRIGGER trg_bot_sessions_updated_at
BEFORE UPDATE ON bot_sessions
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- --------------------------------------------------
-- user_api_keys
-- Зберігати лише зашифровані значення
-- --------------------------------------------------
CREATE TABLE user_api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    exchange TEXT NOT NULL
        CHECK (exchange IN ('binance')),

    label TEXT NOT NULL,

    encrypted_api_key TEXT NOT NULL,
    encrypted_api_secret TEXT NOT NULL,
    encrypted_passphrase TEXT NULL,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_validated_at TIMESTAMPTZ NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_user_api_keys_user_id ON user_api_keys(user_id);
CREATE INDEX idx_user_api_keys_exchange ON user_api_keys(exchange);
CREATE INDEX idx_user_api_keys_is_active ON user_api_keys(is_active);

CREATE TRIGGER trg_user_api_keys_updated_at
BEFORE UPDATE ON user_api_keys
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- --------------------------------------------------
-- billing_webhook_events
-- --------------------------------------------------
CREATE TABLE billing_webhook_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    provider TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,

    status TEXT NOT NULL DEFAULT 'received'
        CHECK (status IN ('received', 'processed', 'failed', 'ignored_duplicate')),

    processed_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_billing_webhook_events_provider_event UNIQUE (provider, event_id)
);

CREATE INDEX idx_billing_webhook_events_provider ON billing_webhook_events(provider);
CREATE INDEX idx_billing_webhook_events_status ON billing_webhook_events(status);
CREATE INDEX idx_billing_webhook_events_created_at ON billing_webhook_events(created_at);

-- --------------------------------------------------
-- admin_audit_logs
-- --------------------------------------------------
CREATE TABLE admin_audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    actor_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    target_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,

    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NULL,

    details_json JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_admin_audit_logs_actor_user_id ON admin_audit_logs(actor_user_id);
CREATE INDEX idx_admin_audit_logs_target_user_id ON admin_audit_logs(target_user_id);
CREATE INDEX idx_admin_audit_logs_action ON admin_audit_logs(action);
CREATE INDEX idx_admin_audit_logs_entity_type ON admin_audit_logs(entity_type);
CREATE INDEX idx_admin_audit_logs_created_at ON admin_audit_logs(created_at);

-- --------------------------------------------------
-- Views
-- --------------------------------------------------
CREATE VIEW v_active_user_subscriptions AS
SELECT
    us.id AS subscription_id,
    us.user_id,
    us.plan_id,
    us.status,
    us.starts_at,
    us.ends_at,
    us.auto_renew,
    us.source,
    sp.code AS plan_code,
    sp.name AS plan_name,
    sp.features_json,
    sp.is_active AS plan_is_active
FROM user_subscriptions us
JOIN subscription_plans sp
    ON sp.id = us.plan_id
WHERE
    us.status = 'active'
    AND sp.is_active = TRUE
    AND (us.ends_at IS NULL OR us.ends_at > NOW());

CREATE VIEW v_enabled_trading_users AS
SELECT
    u.id AS user_id,
    u.email,
    u.display_name,
    u.role,
    u.status AS user_status,
    u.preferred_language,

    tp.telegram_id,
    tp.chat_id,
    tp.username,
    tp.is_onboarded,

    ts.id AS trader_settings_id,
    ts.trading_enabled,
    ts.trading_mode,
    ts.default_stake_mode,
    ts.default_stake_value,
    ts.default_leverage,
    ts.max_open_trades_total,
    ts.max_open_trades_per_symbol,
    ts.allow_long,
    ts.allow_short,

    aus.subscription_id,
    aus.plan_id,
    aus.plan_code,
    aus.plan_name,
    aus.features_json,

    np.notifications_enabled,
    np.notify_trade_opened,
    np.notify_tp_hit,
    np.notify_trade_closed,
    np.notify_stop_loss,
    np.notify_system,
    np.quiet_hours_from,
    np.quiet_hours_to
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
    u.status = 'active'
    AND ts.trading_enabled = TRUE
    AND (
        ts.trading_mode = 'sandbox'
        OR (
            ts.trading_mode = 'live'
            AND COALESCE((aus.features_json ->> 'live_enabled')::boolean, FALSE) = TRUE
        )
    );

CREATE VIEW v_user_telegram_targets AS
SELECT
    u.id AS user_id,
    u.display_name,
    u.preferred_language,
    tp.telegram_id,
    tp.chat_id,
    tp.username,
    tp.is_onboarded,
    np.notifications_enabled,
    np.notify_trade_opened,
    np.notify_tp_hit,
    np.notify_trade_closed,
    np.notify_stop_loss,
    np.notify_system,
    np.quiet_hours_from,
    np.quiet_hours_to
FROM users u
JOIN telegram_profiles tp
    ON tp.user_id = u.id
LEFT JOIN notification_preferences np
    ON np.user_id = u.id
WHERE
    u.status = 'active';

COMMIT;