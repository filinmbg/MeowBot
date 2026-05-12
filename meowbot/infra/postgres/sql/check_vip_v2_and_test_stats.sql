WITH checks AS (
    SELECT
        'subscription_plans_contains_vip_v2' AS check_name,
        EXISTS (
            SELECT 1
            FROM subscription_plans
            WHERE code = 'vip_v2'
        ) AS passed,
        NULL::TEXT AS details
    UNION ALL
    SELECT
        'vip_v2_strategy_version_is_v2' AS check_name,
        EXISTS (
            SELECT 1
            FROM subscription_plans
            WHERE code = 'vip_v2'
            AND COALESCE(features_json, '{}'::jsonb) ->> 'strategy_version' = 'v2'
        ) AS passed,
        NULL::TEXT AS details
    UNION ALL
    SELECT
        'telegram_853048829_active_vip_v2' AS check_name,
        EXISTS (
            SELECT 1
            FROM telegram_profiles tp
            JOIN user_subscriptions us
                ON us.user_id = tp.user_id
            JOIN subscription_plans sp
                ON sp.id = us.plan_id
            WHERE tp.telegram_id = 853048829
            AND us.status = 'active'
            AND sp.code = 'vip_v2'
            AND COALESCE(us.strategy_version, 'v1') = 'v2'
        ) AS passed,
        NULL::TEXT AS details
    UNION ALL
    SELECT
        'telegram_158556807_active_vip_v2' AS check_name,
        EXISTS (
            SELECT 1
            FROM telegram_profiles tp
            JOIN user_subscriptions us
                ON us.user_id = tp.user_id
            JOIN subscription_plans sp
                ON sp.id = us.plan_id
            WHERE tp.telegram_id = 158556807
            AND us.status = 'active'
            AND sp.code = 'vip_v2'
            AND COALESCE(us.strategy_version, 'v1') = 'v2'
        ) AS passed,
        NULL::TEXT AS details
    UNION ALL
    SELECT
        'vip_all_symbols_enabled' AS check_name,
        NOT EXISTS (
            SELECT 1
            FROM subscription_plans sp
            WHERE sp.code IN ('vip', 'vip_v2')
            AND NOT (
                COALESCE((sp.features_json ->> 'can_trade_all_symbols')::boolean, false) = true
                OR LOWER(COALESCE(sp.features_json ->> 'allowed_symbols', '')) = 'all'
                OR (
                    CASE
                        WHEN COALESCE(sp.features_json ->> 'max_symbols', '') ~ '^[0-9]+$'
                            THEN (sp.features_json ->> 'max_symbols')::int
                        ELSE 0
                    END
                ) >= 200
            )
        ) AS passed,
        NULL::TEXT AS details
    UNION ALL
    SELECT
        'v1_test_users_exist' AS check_name,
        NOT EXISTS (
            SELECT 1
            FROM (
                VALUES
                    ('free_test@example.com'),
                    ('basic_test@example.com'),
                    ('pro_test@example.com'),
                    ('vip_test@example.com')
            ) AS expected(email)
            WHERE NOT EXISTS (
                SELECT 1
                FROM users u
                WHERE LOWER(u.email) = expected.email
            )
        ) AS passed,
        NULL::TEXT AS details
    UNION ALL
    SELECT
        'v2_test_users_exist' AS check_name,
        NOT EXISTS (
            SELECT 1
            FROM (
                VALUES
                    ('free_test_v2@example.com'),
                    ('basic_test_v2@example.com'),
                    ('pro_test_v2@example.com'),
                    ('vip_test_v2@example.com')
            ) AS expected(email)
            WHERE NOT EXISTS (
                SELECT 1
                FROM users u
                WHERE LOWER(u.email) = expected.email
            )
        ) AS passed,
        NULL::TEXT AS details
)
SELECT *
FROM checks
ORDER BY check_name;

SELECT
    sp.code AS plan_code,
    sp.features_json ->> 'strategy_version' AS strategy_version,
    sp.features_json ->> 'allowed_symbols' AS allowed_symbols,
    sp.features_json ->> 'can_trade_all_symbols' AS can_trade_all_symbols,
    sp.features_json ->> 'max_symbols' AS max_symbols,
    sp.features_json -> 'enabled_strategy_rules' AS enabled_strategy_rules
FROM subscription_plans sp
WHERE sp.code IN ('vip', 'vip_v2')
ORDER BY sp.code;

SELECT
    tp.telegram_id,
    u.email,
    sp.code AS plan_code,
    us.status,
    COALESCE(us.strategy_version, 'v1') AS strategy_version,
    COALESCE(ts.trading_enabled, false) AS trading_enabled,
    ts.trading_mode
FROM telegram_profiles tp
JOIN users u
    ON u.id = tp.user_id
LEFT JOIN user_subscriptions us
    ON us.user_id = u.id
    AND us.status = 'active'
LEFT JOIN subscription_plans sp
    ON sp.id = us.plan_id
LEFT JOIN trader_settings ts
    ON ts.user_id = u.id
WHERE tp.telegram_id IN (853048829, 158556807)
ORDER BY tp.telegram_id;

SELECT
    CASE
        WHEN COALESCE(us.strategy_version, 'v1') = 'v2'
          OR sp.code LIKE '%\_v2'
          OR LOWER(u.email) LIKE '%\_test\_v2@example.com'
            THEN 'v2'
        ELSE 'v1'
    END AS strategy_version,
    u.email,
    tp.username,
    sp.code AS plan_code,
    us.status,
    COALESCE(ts.trading_enabled, false) AS trading_enabled,
    ts.trading_mode
FROM users u
JOIN telegram_profiles tp
    ON tp.user_id = u.id
LEFT JOIN user_subscriptions us
    ON us.user_id = u.id
    AND us.status = 'active'
LEFT JOIN subscription_plans sp
    ON sp.id = us.plan_id
LEFT JOIN trader_settings ts
    ON ts.user_id = u.id
WHERE LOWER(u.email) IN (
    'free_test@example.com',
    'basic_test@example.com',
    'pro_test@example.com',
    'vip_test@example.com',
    'free_test_v2@example.com',
    'basic_test_v2@example.com',
    'pro_test_v2@example.com',
    'vip_test_v2@example.com'
)
ORDER BY
    CASE
        WHEN COALESCE(us.strategy_version, 'v1') = 'v2'
          OR sp.code LIKE '%\_v2'
          OR LOWER(u.email) LIKE '%\_test\_v2@example.com'
            THEN 2
        ELSE 1
    END,
    CASE regexp_replace(LOWER(COALESCE(sp.code, 'unknown')), '_v2$', '')
        WHEN 'free' THEN 1
        WHEN 'basic' THEN 2
        WHEN 'pro' THEN 3
        WHEN 'vip' THEN 4
        ELSE 99
    END,
    u.email;
