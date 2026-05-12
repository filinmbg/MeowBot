SELECT
    code,
    name,
    price_usd,
    duration_days,
    is_active,
    sort_order,
    features_json ->> 'strategy_version' AS strategy_version,
    features_json -> 'enabled_strategy_rules' AS enabled_strategy_rules
FROM subscription_plans
WHERE code IN (
    'free',
    'basic',
    'pro',
    'vip',
    'trial_pro',
    'free_v2',
    'basic_v2',
    'pro_v2',
    'vip_v2',
    'trial_pro_v2'
)
ORDER BY
    CASE regexp_replace(code, '_v2$', '')
        WHEN 'free' THEN 1
        WHEN 'trial_pro' THEN 2
        WHEN 'basic' THEN 3
        WHEN 'pro' THEN 4
        WHEN 'vip' THEN 5
        ELSE 99
    END,
    code;

SELECT
    u.email,
    tp.username,
    tp.telegram_id,
    sp.code AS plan_code,
    COALESCE(us.strategy_version, 'v1') AS strategy_version,
    us.status,
    ts.trading_enabled,
    ts.trading_mode,
    ts.sandbox_start_balance_usd,
    ts.max_risk_trades
FROM users u
JOIN telegram_profiles tp
    ON tp.user_id = u.id
JOIN user_subscriptions us
    ON us.user_id = u.id
JOIN subscription_plans sp
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
AND us.status = 'active'
ORDER BY
    COALESCE(us.strategy_version, 'v1'),
    CASE regexp_replace(sp.code, '_v2$', '')
        WHEN 'free' THEN 1
        WHEN 'basic' THEN 2
        WHEN 'pro' THEN 3
        WHEN 'vip' THEN 4
        ELSE 99
    END,
    u.email;

SELECT
    'missing_v2_plans' AS check_name,
    ARRAY(
        SELECT expected_code
        FROM (
            VALUES
                ('free_v2'),
                ('basic_v2'),
                ('pro_v2'),
                ('vip_v2'),
                ('trial_pro_v2')
        ) AS expected(expected_code)
        WHERE NOT EXISTS (
            SELECT 1
            FROM subscription_plans sp
            WHERE sp.code = expected.expected_code
        )
    ) AS missing_values
UNION ALL
SELECT
    'missing_v2_test_users' AS check_name,
    ARRAY(
        SELECT expected_email
        FROM (
            VALUES
                ('free_test_v2@example.com'),
                ('basic_test_v2@example.com'),
                ('pro_test_v2@example.com'),
                ('vip_test_v2@example.com')
        ) AS expected(expected_email)
        WHERE NOT EXISTS (
            SELECT 1
            FROM users u
            WHERE LOWER(u.email) = expected.expected_email
        )
    ) AS missing_values
UNION ALL
SELECT
    'v2_users_not_linked_to_v2_plans' AS check_name,
    ARRAY(
        SELECT u.email
        FROM users u
        JOIN user_subscriptions us
            ON us.user_id = u.id
        JOIN subscription_plans sp
            ON sp.id = us.plan_id
        WHERE LOWER(u.email) IN (
            'free_test_v2@example.com',
            'basic_test_v2@example.com',
            'pro_test_v2@example.com',
            'vip_test_v2@example.com'
        )
        AND us.status = 'active'
        AND (
            COALESCE(us.strategy_version, 'v1') <> 'v2'
            OR RIGHT(sp.code, 3) <> '_v2'
        )
    ) AS missing_values;
