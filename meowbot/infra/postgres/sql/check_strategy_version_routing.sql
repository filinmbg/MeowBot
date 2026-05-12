SELECT
    tp.telegram_id,
    u.email,
    tp.username,
    sp.code AS plan_code,
    COALESCE(us.strategy_version, 'v1') AS subscription_strategy_version,
    COALESCE(sp.features_json, '{}'::jsonb) ->> 'strategy_version' AS plan_strategy_version,
    CASE
        WHEN LOWER(sp.code) LIKE '%\_v2' ESCAPE '\'
            OR COALESCE(sp.features_json, '{}'::jsonb) ->> 'strategy_version' = 'v2'
            OR COALESCE(us.strategy_version, 'v1') = 'v2'
        THEN 'v2'
        ELSE 'v1'
    END AS effective_strategy_version,
    us.status
FROM telegram_profiles tp
JOIN users u
    ON u.id = tp.user_id
LEFT JOIN user_subscriptions us
    ON us.user_id = u.id
    AND us.status = 'active'
LEFT JOIN subscription_plans sp
    ON sp.id = us.plan_id
WHERE tp.telegram_id IN (
    900011201,
    900011301,
    900012001,
    900012002,
    900012003,
    900012004,
    900001001,
    900001002,
    900001003,
    900001004
)
OR LOWER(u.email) IN (
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
    effective_strategy_version,
    plan_code,
    u.email,
    tp.telegram_id;

