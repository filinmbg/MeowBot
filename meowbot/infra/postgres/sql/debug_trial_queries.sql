-- 1. Усі плани
SELECT code, name, price_usd, duration_days, features_json
FROM subscription_plans
ORDER BY sort_order;

-- 2. Trial-плани
SELECT code, name, features_json
FROM subscription_plans
WHERE COALESCE((features_json ->> 'is_trial')::boolean, FALSE) = TRUE;

-- 3. Активні trial users
SELECT *
FROM v_active_trial_users
ORDER BY starts_at DESC NULLS LAST;

-- 4. Історія використання trial
SELECT *
FROM trial_consumptions
ORDER BY created_at DESC;

-- 5. Blocklist
SELECT *
FROM trial_blocklist
ORDER BY created_at DESC;

-- 6. API keys: fingerprints + permissions
SELECT
    id,
    user_id,
    exchange,
    label,
    api_key_fingerprint,
    exchange_account_fingerprint,
    permissions_json,
    validation_status,
    permissions_checked_at,
    is_active
FROM user_api_keys
ORDER BY created_at DESC;

-- 7. Усі підписки з trial-ознаками
SELECT
    us.id,
    us.user_id,
    sp.code AS plan_code,
    us.status,
    us.is_trial,
    us.trial_code,
    us.starts_at,
    us.ends_at,
    us.ended_reason
FROM user_subscriptions us
JOIN subscription_plans sp
    ON sp.id = us.plan_id
ORDER BY us.created_at DESC;