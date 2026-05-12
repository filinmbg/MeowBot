BEGIN;

ALTER TABLE user_subscriptions
    ADD COLUMN IF NOT EXISTS strategy_version TEXT;

UPDATE user_subscriptions
SET strategy_version = 'v1'
WHERE strategy_version IS NULL
   OR lower(strategy_version) NOT IN ('v1', 'v2');

ALTER TABLE user_subscriptions
    ALTER COLUMN strategy_version SET DEFAULT 'v1';

ALTER TABLE user_subscriptions
    ALTER COLUMN strategy_version SET NOT NULL;

ALTER TABLE user_subscriptions
    DROP CONSTRAINT IF EXISTS user_subscriptions_strategy_version_check;

ALTER TABLE user_subscriptions
    ADD CONSTRAINT user_subscriptions_strategy_version_check
    CHECK (strategy_version IN ('v1', 'v2'));

CREATE INDEX IF NOT EXISTS idx_user_subscriptions_strategy_version
    ON user_subscriptions(strategy_version);

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
        THEN 'v2'
        WHEN COALESCE(us.strategy_version, 'v1') = 'v2'
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
