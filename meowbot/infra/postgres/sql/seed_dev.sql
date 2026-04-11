BEGIN;

UPDATE users
SET
    display_name = 'Test User',
    role = 'user',
    status = 'active',
    preferred_language = 'uk',
    updated_at = NOW()
WHERE email = 'test@example.com';

INSERT INTO users (
    email,
    display_name,
    role,
    status,
    preferred_language
)
SELECT
    'test@example.com',
    'Test User',
    'user',
    'active',
    'uk'
WHERE NOT EXISTS (
    SELECT 1
    FROM users
    WHERE email = 'test@example.com'
);

INSERT INTO auth_identities (
    user_id,
    provider,
    provider_user_id,
    provider_email,
    is_primary,
    is_verified
)
SELECT
    u.id,
    'email',
    'test@example.com',
    'test@example.com',
    TRUE,
    TRUE
FROM users u
WHERE u.email = 'test@example.com'
ON CONFLICT (provider, provider_user_id) DO NOTHING;

INSERT INTO telegram_profiles (
    user_id,
    telegram_id,
    username,
    first_name,
    last_name,
    chat_id,
    is_onboarded,
    last_seen_at
)
SELECT
    u.id,
    123456789,
    'test_user',
    'Test',
    'User',
    123456789,
    TRUE,
    NOW()
FROM users u
WHERE u.email = 'test@example.com'
ON CONFLICT (user_id) DO UPDATE
SET
    telegram_id = EXCLUDED.telegram_id,
    username = EXCLUDED.username,
    first_name = EXCLUDED.first_name,
    last_name = EXCLUDED.last_name,
    chat_id = EXCLUDED.chat_id,
    is_onboarded = EXCLUDED.is_onboarded,
    last_seen_at = EXCLUDED.last_seen_at,
    updated_at = NOW();

INSERT INTO trader_settings (
    user_id,
    trading_enabled,
    trading_mode,
    default_stake_mode,
    default_stake_value,
    default_leverage,
    max_open_trades_total,
    max_open_trades_per_symbol,
    allow_long,
    allow_short
)
SELECT
    u.id,
    TRUE,
    'sandbox',
    'percent',
    1.0,
    20,
    1,
    1,
    TRUE,
    TRUE
FROM users u
WHERE u.email = 'test@example.com'
ON CONFLICT (user_id) DO UPDATE
SET
    trading_enabled = EXCLUDED.trading_enabled,
    trading_mode = EXCLUDED.trading_mode,
    default_stake_mode = EXCLUDED.default_stake_mode,
    default_stake_value = EXCLUDED.default_stake_value,
    default_leverage = EXCLUDED.default_leverage,
    max_open_trades_total = EXCLUDED.max_open_trades_total,
    max_open_trades_per_symbol = EXCLUDED.max_open_trades_per_symbol,
    allow_long = EXCLUDED.allow_long,
    allow_short = EXCLUDED.allow_short,
    updated_at = NOW();

INSERT INTO notification_preferences (
    user_id,
    notifications_enabled,
    notify_trade_opened,
    notify_tp_hit,
    notify_trade_closed,
    notify_stop_loss,
    notify_system,
    quiet_hours_from,
    quiet_hours_to
)
SELECT
    u.id,
    TRUE,
    TRUE,
    TRUE,
    TRUE,
    TRUE,
    TRUE,
    NULL,
    NULL
FROM users u
WHERE u.email = 'test@example.com'
ON CONFLICT (user_id) DO UPDATE
SET
    notifications_enabled = EXCLUDED.notifications_enabled,
    notify_trade_opened = EXCLUDED.notify_trade_opened,
    notify_tp_hit = EXCLUDED.notify_tp_hit,
    notify_trade_closed = EXCLUDED.notify_trade_closed,
    notify_stop_loss = EXCLUDED.notify_stop_loss,
    notify_system = EXCLUDED.notify_system,
    quiet_hours_from = EXCLUDED.quiet_hours_from,
    quiet_hours_to = EXCLUDED.quiet_hours_to,
    updated_at = NOW();

INSERT INTO user_subscriptions (
    user_id,
    plan_id,
    status,
    starts_at,
    ends_at,
    auto_renew,
    cancelled_at,
    source
)
SELECT
    u.id,
    sp.id,
    'active',
    NOW(),
    NOW() + INTERVAL '30 days',
    FALSE,
    NULL,
    'manual'
FROM users u
JOIN subscription_plans sp ON sp.code = 'free'
WHERE u.email = 'test@example.com'
  AND NOT EXISTS (
      SELECT 1
      FROM user_subscriptions us
      WHERE us.user_id = u.id
        AND us.status = 'active'
  );

INSERT INTO trader_symbol_settings (
    user_id,
    symbol,
    enabled
)
SELECT
    u.id,
    s.symbol,
    TRUE
FROM users u
JOIN subscription_plans sp
    ON sp.code = 'free'
JOIN subscription_plan_symbols s
    ON s.plan_id = sp.id
WHERE u.email = 'test@example.com'
ON CONFLICT (user_id, symbol) DO UPDATE
SET
    enabled = EXCLUDED.enabled,
    updated_at = NOW();

INSERT INTO trader_timeframe_settings (
    user_id,
    timeframe,
    enabled
)
SELECT
    u.id,
    tf.timeframe,
    TRUE
FROM users u
JOIN (
    VALUES
        ('15m'),
        ('30m'),
        ('1h'),
        ('4h'),
        ('1d')
) AS tf(timeframe) ON TRUE
WHERE u.email = 'test@example.com'
ON CONFLICT (user_id, timeframe) DO UPDATE
SET
    enabled = EXCLUDED.enabled,
    updated_at = NOW();

INSERT INTO bot_sessions (
    user_id,
    flow_name,
    step_name,
    state_json
)
SELECT
    u.id,
    'onboarding',
    'completed',
    '{"source":"seed_dev","status":"done"}'::jsonb
FROM users u
WHERE u.email = 'test@example.com'
  AND NOT EXISTS (
      SELECT 1
      FROM bot_sessions bs
      WHERE bs.user_id = u.id
        AND bs.flow_name = 'onboarding'
  );

COMMIT;