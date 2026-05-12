BEGIN;

ALTER TABLE user_subscriptions
    ADD COLUMN IF NOT EXISTS is_trial BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS trial_code TEXT NULL,
    ADD COLUMN IF NOT EXISTS ended_reason TEXT NULL,
    ADD COLUMN IF NOT EXISTS strategy_version TEXT NOT NULL DEFAULT 'v1';

ALTER TABLE trader_settings
    ADD COLUMN IF NOT EXISTS max_risk_trades INTEGER NOT NULL DEFAULT 5,
    ADD COLUMN IF NOT EXISTS sandbox_start_balance_usd NUMERIC(18, 2) NOT NULL DEFAULT 1000.00;

UPDATE subscription_plans vip_v2
SET
    name = COALESCE(vip_v2.name, vip.name || ' V2'),
    price_usd = vip.price_usd,
    duration_days = vip.duration_days,
    features_json = (
        COALESCE(vip.features_json, '{}'::jsonb)
        || jsonb_build_object(
            'strategy_version',
            'v2',
            'base_plan_code',
            'vip',
            'max_symbols',
            200,
            'allowed_symbols',
            'all',
            'can_trade_all_symbols',
            true,
            'enabled_strategy_rules',
            (
                SELECT jsonb_agg(rule ORDER BY rule)
                FROM (
                    SELECT DISTINCT rule
                    FROM (
                        SELECT jsonb_array_elements_text(
                            CASE
                                WHEN jsonb_typeof(COALESCE(vip.features_json, '{}'::jsonb) -> 'enabled_strategy_rules') = 'array'
                                    THEN COALESCE(vip.features_json, '{}'::jsonb) -> 'enabled_strategy_rules'
                                ELSE '[]'::jsonb
                            END
                        ) AS rule
                        UNION ALL
                        SELECT 'LONG_BREAKOUT_V18'
                    ) raw_rules
                ) deduped_rules
            )
        )
    ),
    is_active = vip.is_active,
    sort_order = vip.sort_order,
    updated_at = NOW()
FROM subscription_plans vip
WHERE vip.code = 'vip'
AND vip_v2.code = 'vip_v2';

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
    'vip_v2',
    vip.name || ' V2',
    vip.price_usd,
    vip.duration_days,
    (
        COALESCE(vip.features_json, '{}'::jsonb)
        || jsonb_build_object(
            'strategy_version',
            'v2',
            'base_plan_code',
            'vip',
            'max_symbols',
            200,
            'allowed_symbols',
            'all',
            'can_trade_all_symbols',
            true,
            'enabled_strategy_rules',
            (
                SELECT jsonb_agg(rule ORDER BY rule)
                FROM (
                    SELECT DISTINCT rule
                    FROM (
                        SELECT jsonb_array_elements_text(
                            CASE
                                WHEN jsonb_typeof(COALESCE(vip.features_json, '{}'::jsonb) -> 'enabled_strategy_rules') = 'array'
                                    THEN COALESCE(vip.features_json, '{}'::jsonb) -> 'enabled_strategy_rules'
                                ELSE '[]'::jsonb
                            END
                        ) AS rule
                        UNION ALL
                        SELECT 'LONG_BREAKOUT_V18'
                    ) raw_rules
                ) deduped_rules
            )
        )
    ),
    vip.is_active,
    vip.sort_order
FROM subscription_plans vip
WHERE vip.code = 'vip'
AND NOT EXISTS (
    SELECT 1
    FROM subscription_plans existing_plan
    WHERE existing_plan.code = 'vip_v2'
);

UPDATE subscription_plans
SET
    features_json = (
        COALESCE(features_json, '{}'::jsonb)
        || jsonb_build_object(
            'strategy_version',
            CASE WHEN code = 'vip_v2' THEN 'v2' ELSE 'v1' END,
            'max_symbols',
            200,
            'allowed_symbols',
            'all',
            'can_trade_all_symbols',
            true
        )
    ),
    updated_at = NOW()
WHERE code IN ('vip', 'vip_v2');

UPDATE subscription_plans
SET
    features_json = jsonb_set(
        COALESCE(features_json, '{}'::jsonb),
        '{enabled_strategy_rules}',
        (
            SELECT jsonb_agg(rule ORDER BY rule)
            FROM (
                SELECT DISTINCT rule
                FROM (
                    SELECT jsonb_array_elements_text(
                        CASE
                            WHEN jsonb_typeof(COALESCE(features_json, '{}'::jsonb) -> 'enabled_strategy_rules') = 'array'
                                THEN COALESCE(features_json, '{}'::jsonb) -> 'enabled_strategy_rules'
                            ELSE '[]'::jsonb
                        END
                    ) AS rule
                    UNION ALL
                    SELECT 'LONG_BREAKOUT_V18'
                ) raw_rules
            ) deduped_rules
        ),
        true
    ),
    updated_at = NOW()
WHERE code = 'vip_v2';

CREATE TEMP TABLE tmp_vip_v2_user_ids (
    user_id UUID NOT NULL
) ON COMMIT DROP;

INSERT INTO tmp_vip_v2_user_ids (user_id)
VALUES
    ('5e655456-d28b-4146-9c77-471a2429eea8'::uuid),
    ('8d19bea9-12c9-4253-9b73-daa830f825b6'::uuid);

UPDATE users u
SET
    status = 'active',
    updated_at = NOW()
FROM tmp_vip_v2_user_ids target
WHERE u.id = target.user_id;

WITH desired_plan AS (
    SELECT id AS plan_id
    FROM subscription_plans
    WHERE code = 'vip_v2'
    LIMIT 1
)
UPDATE user_subscriptions us
SET
    status = 'cancelled',
    cancelled_at = COALESCE(us.cancelled_at, NOW()),
    ended_reason = 'replaced_by_vip_v2_user_id_migration',
    updated_at = NOW()
FROM tmp_vip_v2_user_ids target, desired_plan dp
WHERE us.user_id = target.user_id
AND us.status = 'active'
AND (
    us.plan_id IS DISTINCT FROM dp.plan_id
    OR COALESCE(us.strategy_version, 'v1') <> 'v2'
);

WITH desired_plan AS (
    SELECT id AS plan_id
    FROM subscription_plans
    WHERE code = 'vip_v2'
    LIMIT 1
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
    strategy_version = 'v2',
    updated_at = NOW()
FROM tmp_vip_v2_user_ids target, desired_plan dp
WHERE us.user_id = target.user_id
AND us.status = 'active'
AND us.plan_id = dp.plan_id;

WITH desired_plan AS (
    SELECT id AS plan_id
    FROM subscription_plans
    WHERE code = 'vip_v2'
    LIMIT 1
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
    target.user_id,
    dp.plan_id,
    'active',
    NOW(),
    NULL,
    FALSE,
    'admin',
    FALSE,
    NULL,
    'v2'
FROM tmp_vip_v2_user_ids target, desired_plan dp
WHERE EXISTS (
    SELECT 1
    FROM users u
    WHERE u.id = target.user_id
)
AND NOT EXISTS (
    SELECT 1
    FROM user_subscriptions us
    WHERE us.user_id = target.user_id
    AND us.status = 'active'
    AND us.plan_id = dp.plan_id
    AND COALESCE(us.strategy_version, 'v1') = 'v2'
);

UPDATE trader_settings ts
SET
    trading_enabled = TRUE,
    updated_at = NOW()
FROM tmp_vip_v2_user_ids target
WHERE ts.user_id = target.user_id;

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
    target.user_id,
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
FROM tmp_vip_v2_user_ids target
WHERE EXISTS (
    SELECT 1
    FROM users u
    WHERE u.id = target.user_id
)
AND NOT EXISTS (
    SELECT 1
    FROM trader_settings ts
    WHERE ts.user_id = target.user_id
);

SELECT
    target.user_id,
    u.email,
    tp.telegram_id,
    sp.code AS plan_code,
    us.status AS subscription_status,
    COALESCE(us.strategy_version, 'v1') AS strategy_version,
    COALESCE(ts.trading_enabled, false) AS trading_enabled,
    ts.trading_mode
FROM tmp_vip_v2_user_ids target
LEFT JOIN users u
    ON u.id = target.user_id
LEFT JOIN telegram_profiles tp
    ON tp.user_id = u.id
LEFT JOIN user_subscriptions us
    ON us.user_id = u.id
    AND us.status = 'active'
LEFT JOIN subscription_plans sp
    ON sp.id = us.plan_id
LEFT JOIN trader_settings ts
    ON ts.user_id = u.id
ORDER BY target.user_id;

COMMIT;
