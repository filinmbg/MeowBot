-- --------------------------------------------------
-- 1. Усі таблиці
-- --------------------------------------------------
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
ORDER BY table_name;

-- --------------------------------------------------
-- 2. Усі view
-- --------------------------------------------------
SELECT table_name
FROM information_schema.views
WHERE table_schema = 'public'
ORDER BY table_name;

-- --------------------------------------------------
-- 3. Усі плани
-- --------------------------------------------------
SELECT
    id,
    code,
    name,
    price_usd,
    duration_days,
    features_json,
    is_active,
    sort_order
FROM subscription_plans
ORDER BY sort_order;

-- --------------------------------------------------
-- 4. Монети по тарифах
-- --------------------------------------------------
SELECT
    sp.code AS plan_code,
    sps.symbol
FROM subscription_plan_symbols sps
JOIN subscription_plans sp
    ON sp.id = sps.plan_id
ORDER BY sp.sort_order, sps.symbol;

-- --------------------------------------------------
-- 5. Усі користувачі
-- --------------------------------------------------
SELECT
    id,
    email,
    display_name,
    role,
    status,
    preferred_language,
    created_at,
    updated_at
FROM users
ORDER BY created_at DESC;

-- --------------------------------------------------
-- 6. Telegram-профілі
-- --------------------------------------------------
SELECT
    tp.id,
    tp.user_id,
    tp.telegram_id,
    tp.username,
    tp.first_name,
    tp.last_name,
    tp.chat_id,
    tp.is_onboarded,
    tp.last_seen_at
FROM telegram_profiles tp
ORDER BY tp.created_at DESC;

-- --------------------------------------------------
-- 7. auth identities
-- --------------------------------------------------
SELECT
    ai.id,
    ai.user_id,
    ai.provider,
    ai.provider_user_id,
    ai.provider_email,
    ai.is_primary,
    ai.is_verified
FROM auth_identities ai
ORDER BY ai.created_at DESC;

-- --------------------------------------------------
-- 8. trader settings
-- --------------------------------------------------
SELECT
    ts.*
FROM trader_settings ts
ORDER BY ts.created_at DESC;

-- --------------------------------------------------
-- 9. symbol settings
-- --------------------------------------------------
SELECT
    tss.user_id,
    tss.symbol,
    tss.enabled
FROM trader_symbol_settings tss
ORDER BY tss.user_id, tss.symbol;

-- --------------------------------------------------
-- 10. timeframe settings
-- --------------------------------------------------
SELECT
    tfs.user_id,
    tfs.timeframe,
    tfs.enabled
FROM trader_timeframe_settings tfs
ORDER BY tfs.user_id, tfs.timeframe;

-- --------------------------------------------------
-- 11. notification preferences
-- --------------------------------------------------
SELECT
    np.*
FROM notification_preferences np
ORDER BY np.created_at DESC;

-- --------------------------------------------------
-- 12. user subscriptions
-- --------------------------------------------------
SELECT
    us.id,
    us.user_id,
    sp.code AS plan_code,
    sp.name AS plan_name,
    us.status,
    us.starts_at,
    us.ends_at,
    us.auto_renew,
    us.cancelled_at,
    us.source
FROM user_subscriptions us
JOIN subscription_plans sp
    ON sp.id = us.plan_id
ORDER BY us.created_at DESC;

-- --------------------------------------------------
-- 13. active subscriptions view
-- --------------------------------------------------
SELECT *
FROM v_active_user_subscriptions
ORDER BY starts_at DESC NULLS LAST;

-- --------------------------------------------------
-- 14. enabled trading users view
-- --------------------------------------------------
SELECT *
FROM v_enabled_trading_users
ORDER BY user_id;

-- --------------------------------------------------
-- 15. telegram targets view
-- --------------------------------------------------
SELECT *
FROM v_user_telegram_targets
ORDER BY user_id;

-- --------------------------------------------------
-- 16. allowed plan symbols vs enabled user symbols
-- --------------------------------------------------
SELECT
    u.email,
    sp.code AS plan_code,
    sps.symbol AS allowed_symbol,
    COALESCE(tss.enabled, FALSE) AS user_enabled
FROM users u
JOIN user_subscriptions us
    ON us.user_id = u.id
   AND us.status = 'active'
JOIN subscription_plans sp
    ON sp.id = us.plan_id
JOIN subscription_plan_symbols sps
    ON sps.plan_id = sp.id
LEFT JOIN trader_symbol_settings tss
    ON tss.user_id = u.id
   AND tss.symbol = sps.symbol
ORDER BY u.email, sps.symbol;

-- --------------------------------------------------
-- 17. user api keys
-- --------------------------------------------------
SELECT
    id,
    user_id,
    exchange,
    label,
    is_active,
    last_validated_at,
    created_at,
    updated_at
FROM user_api_keys
ORDER BY created_at DESC;

-- --------------------------------------------------
-- 18. payments
-- --------------------------------------------------
SELECT
    p.id,
    p.user_id,
    p.subscription_id,
    p.provider,
    p.provider_payment_id,
    p.currency,
    p.amount,
    p.status,
    p.paid_at,
    p.created_at
FROM payments p
ORDER BY p.created_at DESC;

-- --------------------------------------------------
-- 19. billing webhook events
-- --------------------------------------------------
SELECT
    id,
    provider,
    event_id,
    event_type,
    status,
    processed_at,
    created_at
FROM billing_webhook_events
ORDER BY created_at DESC;

-- --------------------------------------------------
-- 20. admin audit logs
-- --------------------------------------------------
SELECT
    id,
    actor_user_id,
    target_user_id,
    action,
    entity_type,
    entity_id,
    details_json,
    created_at
FROM admin_audit_logs
ORDER BY created_at DESC;

-- --------------------------------------------------
-- 21. bot sessions
-- --------------------------------------------------
SELECT
    id,
    user_id,
    flow_name,
    step_name,
    state_json,
    created_at,
    updated_at
FROM bot_sessions
ORDER BY updated_at DESC;

-- --------------------------------------------------
-- 22. Знайти юзера за email
-- --------------------------------------------------
SELECT *
FROM users
WHERE email = 'test@example.com';

-- --------------------------------------------------
-- 23. Активний план конкретного юзера
-- --------------------------------------------------
SELECT
    u.email,
    sp.code,
    sp.name,
    us.status,
    us.starts_at,
    us.ends_at
FROM users u
JOIN user_subscriptions us
    ON us.user_id = u.id
JOIN subscription_plans sp
    ON sp.id = us.plan_id
WHERE u.email = 'test@example.com'
  AND us.status = 'active';

-- --------------------------------------------------
-- 24. Чи дозволений live mode для юзера
-- --------------------------------------------------
SELECT
    u.email,
    ts.trading_mode,
    COALESCE((sp.features_json ->> 'live_enabled')::boolean, FALSE) AS live_enabled_by_plan
FROM users u
JOIN trader_settings ts
    ON ts.user_id = u.id
JOIN user_subscriptions us
    ON us.user_id = u.id
   AND us.status = 'active'
JOIN subscription_plans sp
    ON sp.id = us.plan_id
WHERE u.email = 'test@example.com';

-- --------------------------------------------------
-- 25. Які монети реально увімкнені юзеру
-- --------------------------------------------------
SELECT
    u.email,
    tss.symbol,
    tss.enabled
FROM users u
JOIN trader_symbol_settings tss
    ON tss.user_id = u.id
WHERE u.email = 'test@example.com'
ORDER BY tss.symbol;