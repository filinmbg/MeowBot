BEGIN;

ALTER TABLE user_subscriptions
    ADD COLUMN IF NOT EXISTS strategy_version TEXT NOT NULL DEFAULT 'v1';

UPDATE user_subscriptions us
SET
    strategy_version = 'v2',
    updated_at = NOW()
FROM subscription_plans sp
LEFT JOIN users u
    ON u.id = us.user_id
LEFT JOIN telegram_profiles tp
    ON tp.user_id = us.user_id
WHERE us.plan_id = sp.id
AND us.status = 'active'
AND COALESCE(us.strategy_version, 'v1') <> 'v2'
AND (
    LOWER(sp.code) LIKE '%\_v2' ESCAPE '\'
    OR COALESCE(sp.features_json, '{}'::jsonb) ->> 'strategy_version' = 'v2'
    OR LOWER(COALESCE(u.email, '')) LIKE '%\_v2@example.com' ESCAPE '\'
    OR LOWER(COALESCE(tp.username, '')) LIKE '%\_v2' ESCAPE '\'
    OR tp.telegram_id IN (
        900012001,
        900012002,
        900012003,
        900012004
    )
);

UPDATE user_subscriptions us
SET
    strategy_version = 'v1',
    updated_at = NOW()
FROM subscription_plans sp
LEFT JOIN users u
    ON u.id = us.user_id
LEFT JOIN telegram_profiles tp
    ON tp.user_id = us.user_id
WHERE us.plan_id = sp.id
AND us.status = 'active'
AND COALESCE(us.strategy_version, 'v1') <> 'v1'
AND LOWER(sp.code) NOT LIKE '%\_v2' ESCAPE '\'
AND COALESCE(sp.features_json, '{}'::jsonb) ->> 'strategy_version' IS DISTINCT FROM 'v2'
AND LOWER(COALESCE(u.email, '')) IN (
    'free_test@example.com',
    'basic_test@example.com',
    'pro_test@example.com',
    'vip_test@example.com'
)
AND COALESCE(tp.telegram_id, 0) IN (
    900001001,
    900001002,
    900001003,
    900001004
);

CREATE OR REPLACE VIEW v_active_user_subscriptions AS
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
    sp.is_active AS plan_is_active,
    CASE
        WHEN LOWER(sp.code) LIKE '%\_v2' ESCAPE '\'
            OR COALESCE(sp.features_json, '{}'::jsonb) ->> 'strategy_version' = 'v2'
            OR COALESCE(us.strategy_version, 'v1') = 'v2'
        THEN 'v2'
        ELSE 'v1'
    END AS strategy_version
FROM user_subscriptions us
JOIN subscription_plans sp
    ON sp.id = us.plan_id
WHERE
    us.status = 'active'
    AND sp.is_active = TRUE
    AND (us.ends_at IS NULL OR us.ends_at > NOW());

COMMIT;
