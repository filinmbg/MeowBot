BEGIN;

CREATE TEMP TABLE tmp_strategy_version_plan_pairs (
    base_code TEXT NOT NULL,
    v2_code TEXT NOT NULL
) ON COMMIT DROP;

INSERT INTO tmp_strategy_version_plan_pairs (base_code, v2_code)
VALUES
    ('free', 'free_v2'),
    ('basic', 'basic_v2'),
    ('pro', 'pro_v2'),
    ('vip', 'vip_v2'),
    ('trial_pro', 'trial_pro_v2');

WITH v2_plan_rows AS (
    SELECT
        pairs.base_code,
        pairs.v2_code,
        src.name || ' V2' AS name,
        src.price_usd,
        src.duration_days,
        (
            COALESCE(src.features_json, '{}'::jsonb)
            || jsonb_build_object(
                'strategy_version',
                'v2',
                'base_plan_code',
                pairs.base_code,
                'enabled_strategy_rules',
                (
                    SELECT jsonb_agg(rule ORDER BY rule)
                    FROM (
                        SELECT DISTINCT rule
                        FROM (
                            SELECT jsonb_array_elements_text(
                                CASE
                                    WHEN jsonb_typeof(COALESCE(src.features_json, '{}'::jsonb) -> 'enabled_strategy_rules') = 'array'
                                        THEN COALESCE(src.features_json, '{}'::jsonb) -> 'enabled_strategy_rules'
                                    ELSE '[]'::jsonb
                                END
                            ) AS rule
                            UNION ALL
                            SELECT 'LONG_BREAKOUT_V18'
                        ) raw_rules
                    ) deduped_rules
                )
            )
        ) AS features_json,
        src.is_active,
        src.sort_order
    FROM tmp_strategy_version_plan_pairs pairs
    JOIN subscription_plans src
        ON LOWER(src.code) = LOWER(pairs.base_code)
)
UPDATE subscription_plans dst
SET
    name = v2_plan_rows.name,
    price_usd = v2_plan_rows.price_usd,
    duration_days = v2_plan_rows.duration_days,
    features_json = v2_plan_rows.features_json,
    is_active = v2_plan_rows.is_active,
    sort_order = v2_plan_rows.sort_order,
    updated_at = NOW()
FROM v2_plan_rows
WHERE LOWER(dst.code) = LOWER(v2_plan_rows.v2_code);

WITH v2_plan_rows AS (
    SELECT
        pairs.base_code,
        pairs.v2_code,
        src.name || ' V2' AS name,
        src.price_usd,
        src.duration_days,
        (
            COALESCE(src.features_json, '{}'::jsonb)
            || jsonb_build_object(
                'strategy_version',
                'v2',
                'base_plan_code',
                pairs.base_code,
                'enabled_strategy_rules',
                (
                    SELECT jsonb_agg(rule ORDER BY rule)
                    FROM (
                        SELECT DISTINCT rule
                        FROM (
                            SELECT jsonb_array_elements_text(
                                CASE
                                    WHEN jsonb_typeof(COALESCE(src.features_json, '{}'::jsonb) -> 'enabled_strategy_rules') = 'array'
                                        THEN COALESCE(src.features_json, '{}'::jsonb) -> 'enabled_strategy_rules'
                                    ELSE '[]'::jsonb
                                END
                            ) AS rule
                            UNION ALL
                            SELECT 'LONG_BREAKOUT_V18'
                        ) raw_rules
                    ) deduped_rules
                )
            )
        ) AS features_json,
        src.is_active,
        src.sort_order
    FROM tmp_strategy_version_plan_pairs pairs
    JOIN subscription_plans src
        ON LOWER(src.code) = LOWER(pairs.base_code)
)
INSERT INTO subscription_plans (
    code,
    name,
    price_usd,
    duration_days,
    features_json,
    is_active,
    sort_order
)
SELECT
    v2_plan_rows.v2_code,
    v2_plan_rows.name,
    v2_plan_rows.price_usd,
    v2_plan_rows.duration_days,
    v2_plan_rows.features_json,
    v2_plan_rows.is_active,
    v2_plan_rows.sort_order
FROM v2_plan_rows
WHERE NOT EXISTS (
    SELECT 1
    FROM subscription_plans existing_plan
    WHERE LOWER(existing_plan.code) = LOWER(v2_plan_rows.v2_code)
);

DELETE FROM subscription_plan_symbols s
USING subscription_plans v2_plan
WHERE s.plan_id = v2_plan.id
AND LOWER(v2_plan.code) IN (
    SELECT LOWER(v2_code)
    FROM tmp_strategy_version_plan_pairs
);

INSERT INTO subscription_plan_symbols (plan_id, symbol)
SELECT
    v2_plan.id,
    base_symbols.symbol
FROM tmp_strategy_version_plan_pairs pairs
JOIN subscription_plans base_plan
    ON LOWER(base_plan.code) = LOWER(pairs.base_code)
JOIN subscription_plans v2_plan
    ON LOWER(v2_plan.code) = LOWER(pairs.v2_code)
JOIN subscription_plan_symbols base_symbols
    ON base_symbols.plan_id = base_plan.id
WHERE NOT EXISTS (
    SELECT 1
    FROM subscription_plan_symbols existing_symbol
    WHERE existing_symbol.plan_id = v2_plan.id
    AND existing_symbol.symbol = base_symbols.symbol
);

CREATE TEMP TABLE tmp_strategy_version_test_users (
    email TEXT NOT NULL,
    display_name TEXT NOT NULL,
    telegram_id BIGINT NOT NULL,
    username TEXT NOT NULL,
    plan_code TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    manage_existing BOOLEAN NOT NULL
) ON COMMIT DROP;

INSERT INTO tmp_strategy_version_test_users (
    email,
    display_name,
    telegram_id,
    username,
    plan_code,
    strategy_version,
    manage_existing
)
VALUES
    ('free_test@example.com', 'FREE Test User', 900001001, 'free_test_user', 'free', 'v1', FALSE),
    ('basic_test@example.com', 'BASIC Test User', 900001002, 'basic_test_user', 'basic', 'v1', FALSE),
    ('pro_test@example.com', 'PRO Test User', 900001003, 'pro_test_user', 'pro', 'v1', FALSE),
    ('vip_test@example.com', 'VIP Test User', 900001004, 'vip_test_user', 'vip', 'v1', FALSE),
    ('free_test_v2@example.com', 'FREE Test V2', 900012001, 'free_test_v2', 'free_v2', 'v2', TRUE),
    ('basic_test_v2@example.com', 'BASIC Test V2', 900012002, 'basic_test_v2', 'basic_v2', 'v2', TRUE),
    ('pro_test_v2@example.com', 'PRO Test V2', 900012003, 'pro_test_v2', 'pro_v2', 'v2', TRUE),
    ('vip_test_v2@example.com', 'VIP Test V2', 900012004, 'vip_test_v2', 'vip_v2', 'v2', TRUE);

UPDATE users u
SET
    display_name = t.display_name,
    role = 'user',
    status = 'active',
    preferred_language = COALESCE(u.preferred_language, 'uk'),
    updated_at = NOW()
FROM tmp_strategy_version_test_users t
WHERE LOWER(u.email) = LOWER(t.email)
AND t.manage_existing = TRUE;

INSERT INTO users (
    email,
    display_name,
    role,
    status,
    preferred_language
)
SELECT
    t.email,
    t.display_name,
    'user',
    'active',
    'uk'
FROM tmp_strategy_version_test_users t
WHERE NOT EXISTS (
    SELECT 1
    FROM users u
    WHERE LOWER(u.email) = LOWER(t.email)
);

WITH selected_users AS (
    SELECT
        u.id AS user_id,
        t.email,
        t.manage_existing
    FROM tmp_strategy_version_test_users t
    JOIN LATERAL (
        SELECT id
        FROM users u
        WHERE LOWER(u.email) = LOWER(t.email)
        ORDER BY u.created_at ASC
        LIMIT 1
    ) u ON TRUE
)
UPDATE auth_identities ai
SET
    user_id = selected_users.user_id,
    provider_email = selected_users.email,
    is_primary = TRUE,
    is_verified = TRUE,
    updated_at = NOW()
FROM selected_users
WHERE ai.provider = 'email'
AND LOWER(ai.provider_user_id) = LOWER(selected_users.email)
AND selected_users.manage_existing = TRUE;

WITH selected_users AS (
    SELECT
        u.id AS user_id,
        t.email
    FROM tmp_strategy_version_test_users t
    JOIN LATERAL (
        SELECT id
        FROM users u
        WHERE LOWER(u.email) = LOWER(t.email)
        ORDER BY u.created_at ASC
        LIMIT 1
    ) u ON TRUE
)
INSERT INTO auth_identities (
    user_id,
    provider,
    provider_user_id,
    provider_email,
    is_primary,
    is_verified
)
SELECT
    selected_users.user_id,
    'email',
    selected_users.email,
    selected_users.email,
    TRUE,
    TRUE
FROM selected_users
WHERE NOT EXISTS (
    SELECT 1
    FROM auth_identities ai
    WHERE ai.provider = 'email'
    AND LOWER(ai.provider_user_id) = LOWER(selected_users.email)
);

WITH selected_users AS (
    SELECT
        u.id AS user_id,
        t.telegram_id,
        t.username,
        t.manage_existing
    FROM tmp_strategy_version_test_users t
    JOIN LATERAL (
        SELECT id
        FROM users u
        WHERE LOWER(u.email) = LOWER(t.email)
        ORDER BY u.created_at ASC
        LIMIT 1
    ) u ON TRUE
)
UPDATE telegram_profiles tp
SET
    telegram_id = selected_users.telegram_id,
    username = selected_users.username,
    first_name = selected_users.username,
    last_name = NULL,
    chat_id = selected_users.telegram_id,
    is_onboarded = TRUE,
    last_seen_at = NOW(),
    updated_at = NOW()
FROM selected_users
WHERE tp.user_id = selected_users.user_id
AND selected_users.manage_existing = TRUE
AND NOT EXISTS (
    SELECT 1
    FROM telegram_profiles other_tp
    WHERE other_tp.telegram_id = selected_users.telegram_id
    AND other_tp.user_id <> selected_users.user_id
);

WITH selected_users AS (
    SELECT
        u.id AS user_id,
        t.telegram_id,
        t.username
    FROM tmp_strategy_version_test_users t
    JOIN LATERAL (
        SELECT id
        FROM users u
        WHERE LOWER(u.email) = LOWER(t.email)
        ORDER BY u.created_at ASC
        LIMIT 1
    ) u ON TRUE
)
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
    selected_users.user_id,
    selected_users.telegram_id,
    selected_users.username,
    selected_users.username,
    NULL,
    selected_users.telegram_id,
    TRUE,
    NOW()
FROM selected_users
WHERE NOT EXISTS (
    SELECT 1
    FROM telegram_profiles tp
    WHERE tp.user_id = selected_users.user_id
)
AND NOT EXISTS (
    SELECT 1
    FROM telegram_profiles tp
    WHERE tp.telegram_id = selected_users.telegram_id
);

WITH selected_users AS (
    SELECT
        u.id AS user_id,
        t.manage_existing
    FROM tmp_strategy_version_test_users t
    JOIN LATERAL (
        SELECT id
        FROM users u
        WHERE LOWER(u.email) = LOWER(t.email)
        ORDER BY u.created_at ASC
        LIMIT 1
    ) u ON TRUE
)
UPDATE trader_settings ts
SET
    trading_enabled = TRUE,
    trading_mode = 'sandbox',
    default_stake_mode = 'percent',
    default_stake_value = 1.0,
    default_leverage = 5,
    max_open_trades_total = 20,
    max_open_trades_per_symbol = 1,
    allow_long = TRUE,
    allow_short = TRUE,
    max_risk_trades = 20,
    sandbox_start_balance_usd = 1000,
    updated_at = NOW()
FROM selected_users
WHERE ts.user_id = selected_users.user_id
AND selected_users.manage_existing = TRUE;

WITH selected_users AS (
    SELECT u.id AS user_id
    FROM tmp_strategy_version_test_users t
    JOIN LATERAL (
        SELECT id
        FROM users u
        WHERE LOWER(u.email) = LOWER(t.email)
        ORDER BY u.created_at ASC
        LIMIT 1
    ) u ON TRUE
)
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
    allow_short,
    max_risk_trades,
    sandbox_start_balance_usd
)
SELECT
    selected_users.user_id,
    TRUE,
    'sandbox',
    'percent',
    1.0,
    5,
    20,
    1,
    TRUE,
    TRUE,
    20,
    1000
FROM selected_users
WHERE NOT EXISTS (
    SELECT 1
    FROM trader_settings ts
    WHERE ts.user_id = selected_users.user_id
);

WITH selected_users AS (
    SELECT
        u.id AS user_id,
        t.manage_existing
    FROM tmp_strategy_version_test_users t
    JOIN LATERAL (
        SELECT id
        FROM users u
        WHERE LOWER(u.email) = LOWER(t.email)
        ORDER BY u.created_at ASC
        LIMIT 1
    ) u ON TRUE
)
UPDATE notification_preferences np
SET
    notifications_enabled = TRUE,
    notify_trade_opened = TRUE,
    notify_tp_hit = TRUE,
    notify_trade_closed = TRUE,
    notify_stop_loss = TRUE,
    notify_system = TRUE,
    quiet_hours_from = NULL,
    quiet_hours_to = NULL,
    updated_at = NOW()
FROM selected_users
WHERE np.user_id = selected_users.user_id
AND selected_users.manage_existing = TRUE;

WITH selected_users AS (
    SELECT u.id AS user_id
    FROM tmp_strategy_version_test_users t
    JOIN LATERAL (
        SELECT id
        FROM users u
        WHERE LOWER(u.email) = LOWER(t.email)
        ORDER BY u.created_at ASC
        LIMIT 1
    ) u ON TRUE
)
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
    selected_users.user_id,
    TRUE,
    TRUE,
    TRUE,
    TRUE,
    TRUE,
    TRUE,
    NULL,
    NULL
FROM selected_users
WHERE NOT EXISTS (
    SELECT 1
    FROM notification_preferences np
    WHERE np.user_id = selected_users.user_id
);

WITH selected_subscriptions AS (
    SELECT
        u.id AS user_id,
        sp.id AS plan_id,
        t.strategy_version,
        t.manage_existing
    FROM tmp_strategy_version_test_users t
    JOIN LATERAL (
        SELECT id
        FROM users u
        WHERE LOWER(u.email) = LOWER(t.email)
        ORDER BY u.created_at ASC
        LIMIT 1
    ) u ON TRUE
    JOIN subscription_plans sp ON LOWER(sp.code) = LOWER(t.plan_code)
)
UPDATE user_subscriptions us
SET
    status = 'cancelled',
    cancelled_at = COALESCE(us.cancelled_at, NOW()),
    ended_reason = 'replaced_by_strategy_version_seed',
    updated_at = NOW()
FROM selected_subscriptions s
WHERE us.user_id = s.user_id
AND us.status = 'active'
AND s.manage_existing = TRUE
AND (
    us.plan_id IS DISTINCT FROM s.plan_id
    OR COALESCE(us.strategy_version, 'v1') <> s.strategy_version
);

WITH selected_subscriptions AS (
    SELECT
        u.id AS user_id,
        sp.id AS plan_id,
        t.strategy_version,
        t.manage_existing
    FROM tmp_strategy_version_test_users t
    JOIN LATERAL (
        SELECT id
        FROM users u
        WHERE LOWER(u.email) = LOWER(t.email)
        ORDER BY u.created_at ASC
        LIMIT 1
    ) u ON TRUE
    JOIN subscription_plans sp ON LOWER(sp.code) = LOWER(t.plan_code)
)
UPDATE user_subscriptions us
SET
    starts_at = COALESCE(us.starts_at, NOW()),
    ends_at = NULL,
    auto_renew = FALSE,
    cancelled_at = NULL,
    source = 'admin',
    is_trial = FALSE,
    trial_code = NULL,
    ended_reason = NULL,
    strategy_version = s.strategy_version,
    updated_at = NOW()
FROM selected_subscriptions s
WHERE us.user_id = s.user_id
AND us.status = 'active'
AND s.manage_existing = TRUE
AND us.plan_id = s.plan_id
AND COALESCE(us.strategy_version, 'v1') = s.strategy_version;

WITH selected_subscriptions AS (
    SELECT
        u.id AS user_id,
        sp.id AS plan_id,
        t.strategy_version
    FROM tmp_strategy_version_test_users t
    JOIN LATERAL (
        SELECT id
        FROM users u
        WHERE LOWER(u.email) = LOWER(t.email)
        ORDER BY u.created_at ASC
        LIMIT 1
    ) u ON TRUE
    JOIN subscription_plans sp ON LOWER(sp.code) = LOWER(t.plan_code)
)
INSERT INTO user_subscriptions (
    user_id,
    plan_id,
    status,
    starts_at,
    ends_at,
    auto_renew,
    source,
    is_trial,
    trial_code,
    strategy_version
)
SELECT
    s.user_id,
    s.plan_id,
    'active',
    NOW(),
    NULL,
    FALSE,
    'admin',
    FALSE,
    NULL,
    s.strategy_version
FROM selected_subscriptions s
WHERE NOT EXISTS (
    SELECT 1
    FROM user_subscriptions us
    WHERE us.user_id = s.user_id
    AND us.status = 'active'
);

COMMIT;
