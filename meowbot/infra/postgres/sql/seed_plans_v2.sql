BEGIN;

-- --------------------------------------------------
-- subscription_plans
-- FREE + TRIAL_PRO + BASIC + PRO + VIP
-- --------------------------------------------------
INSERT INTO subscription_plans (
    code,
    name,
    price_usd,
    duration_days,
    features_json,
    is_active,
    sort_order
)
VALUES
(
    'free',
    'Free',
    0.00,
    36500,
    '{
      "max_symbols": 10,
      "max_open_trades_total": 1,
      "max_open_trades_per_symbol": 1,
      "sandbox_enabled": true,
      "live_enabled": false,
      "is_trial": false,
      "auto_fallback_plan_code": null
    }'::jsonb,
    TRUE,
    1
),
(
    'trial_pro',
    'Trial Pro',
    0.00,
    3,
    '{
      "max_symbols": 50,
      "max_open_trades_total": 5,
      "max_open_trades_per_symbol": 1,
      "sandbox_enabled": true,
      "live_enabled": true,
      "is_trial": true,
      "trial_days": 3,
      "requires_api_keys": true,
      "requires_trading_permission": true,
      "requires_withdrawal_disabled": true,
      "auto_fallback_plan_code": "free"
    }'::jsonb,
    TRUE,
    2
),
(
    'basic',
    'Basic',
    19.99,
    30,
    '{
      "max_symbols": 20,
      "max_open_trades_total": 3,
      "max_open_trades_per_symbol": 1,
      "sandbox_enabled": true,
      "live_enabled": true,
      "is_trial": false,
      "auto_fallback_plan_code": null
    }'::jsonb,
    TRUE,
    3
),
(
    'pro',
    'Pro',
    49.99,
    30,
    '{
      "max_symbols": 100,
      "max_open_trades_total": 20,
      "max_open_trades_per_symbol": 1,
      "sandbox_enabled": true,
      "live_enabled": true,
      "is_trial": false,
      "auto_fallback_plan_code": null
    }'::jsonb,
    TRUE,
    4
),
(
    'vip',
    'VIP',
    99.99,
    30,
    '{
      "max_symbols": 150,
      "max_open_trades_total": null,
      "max_open_trades_per_symbol": 1,
      "sandbox_enabled": true,
      "live_enabled": true,
      "is_trial": false,
      "auto_fallback_plan_code": null,
      "max_symbols_expandable_to": 300
    }'::jsonb,
    TRUE,
    5
)
ON CONFLICT (code) DO UPDATE
SET
    name = EXCLUDED.name,
    price_usd = EXCLUDED.price_usd,
    duration_days = EXCLUDED.duration_days,
    features_json = EXCLUDED.features_json,
    is_active = EXCLUDED.is_active,
    sort_order = EXCLUDED.sort_order,
    updated_at = NOW();

-- --------------------------------------------------
-- Очистити старі plan-symbol mappings
-- --------------------------------------------------
DELETE FROM subscription_plan_symbols
WHERE plan_id IN (
    SELECT id
    FROM subscription_plans
    WHERE code IN ('free', 'trial_pro', 'basic', 'pro', 'vip')
);

-- --------------------------------------------------
-- FREE = 10 монет
-- --------------------------------------------------
INSERT INTO subscription_plan_symbols (plan_id, symbol)
SELECT sp.id, s.symbol
FROM subscription_plans sp
JOIN (
    VALUES
        ('BTCUSDT'),
        ('ETHUSDT'),
        ('BNBUSDT'),
        ('SOLUSDT'),
        ('XRPUSDT'),
        ('ADAUSDT'),
        ('DOGEUSDT'),
        ('TRXUSDT'),
        ('AVAXUSDT'),
        ('LINKUSDT')
) AS s(symbol) ON TRUE
WHERE sp.code = 'free';

-- --------------------------------------------------
-- TRIAL_PRO = 20 монет
-- --------------------------------------------------
INSERT INTO subscription_plan_symbols (plan_id, symbol)
SELECT sp.id, s.symbol
FROM subscription_plans sp
JOIN (
    VALUES
        ('BTCUSDT'),
        ('ETHUSDT'),
        ('BNBUSDT'),
        ('SOLUSDT'),
        ('XRPUSDT'),
        ('ADAUSDT'),
        ('DOGEUSDT'),
        ('TRXUSDT'),
        ('AVAXUSDT'),
        ('LINKUSDT'),
        ('DOTUSDT'),
        ('MATICUSDT'),
        ('LTCUSDT'),
        ('BCHUSDT'),
        ('ATOMUSDT'),
        ('ETCUSDT'),
        ('UNIUSDT'),
        ('FILUSDT'),
        ('APTUSDT'),
        ('NEARUSDT')
) AS s(symbol) ON TRUE
WHERE sp.code = 'trial_pro';

-- --------------------------------------------------
-- BASIC = 20 монет
-- --------------------------------------------------
INSERT INTO subscription_plan_symbols (plan_id, symbol)
SELECT sp.id, s.symbol
FROM subscription_plans sp
JOIN (
    VALUES
        ('BTCUSDT'),
        ('ETHUSDT'),
        ('BNBUSDT'),
        ('SOLUSDT'),
        ('XRPUSDT'),
        ('ADAUSDT'),
        ('DOGEUSDT'),
        ('TRXUSDT'),
        ('AVAXUSDT'),
        ('LINKUSDT'),
        ('DOTUSDT'),
        ('MATICUSDT'),
        ('LTCUSDT'),
        ('BCHUSDT'),
        ('ATOMUSDT'),
        ('ETCUSDT'),
        ('UNIUSDT'),
        ('FILUSDT'),
        ('APTUSDT'),
        ('NEARUSDT')
) AS s(symbol) ON TRUE
WHERE sp.code = 'basic';

-- --------------------------------------------------
-- PRO = 20 монет
-- --------------------------------------------------
INSERT INTO subscription_plan_symbols (plan_id, symbol)
SELECT sp.id, s.symbol
FROM subscription_plans sp
JOIN (
    VALUES
        ('BTCUSDT'),
        ('ETHUSDT'),
        ('BNBUSDT'),
        ('SOLUSDT'),
        ('XRPUSDT'),
        ('ADAUSDT'),
        ('DOGEUSDT'),
        ('TRXUSDT'),
        ('AVAXUSDT'),
        ('LINKUSDT'),
        ('DOTUSDT'),
        ('MATICUSDT'),
        ('LTCUSDT'),
        ('BCHUSDT'),
        ('ATOMUSDT'),
        ('ETCUSDT'),
        ('UNIUSDT'),
        ('FILUSDT'),
        ('APTUSDT'),
        ('NEARUSDT')
) AS s(symbol) ON TRUE
WHERE sp.code = 'pro';

-- --------------------------------------------------
-- VIP = стартово 50 монет
-- --------------------------------------------------
INSERT INTO subscription_plan_symbols (plan_id, symbol)
SELECT sp.id, s.symbol
FROM subscription_plans sp
JOIN (
    VALUES
        ('BTCUSDT'),
        ('ETHUSDT'),
        ('BNBUSDT'),
        ('SOLUSDT'),
        ('XRPUSDT'),
        ('ADAUSDT'),
        ('DOGEUSDT'),
        ('TRXUSDT'),
        ('AVAXUSDT'),
        ('LINKUSDT'),
        ('DOTUSDT'),
        ('MATICUSDT'),
        ('LTCUSDT'),
        ('BCHUSDT'),
        ('ATOMUSDT'),
        ('ETCUSDT'),
        ('UNIUSDT'),
        ('FILUSDT'),
        ('APTUSDT'),
        ('NEARUSDT'),
        ('ARBUSDT'),
        ('OPUSDT'),
        ('INJUSDT'),
        ('SUIUSDT'),
        ('SEIUSDT'),
        ('TIAUSDT'),
        ('RUNEUSDT'),
        ('AAVEUSDT'),
        ('GRTUSDT'),
        ('ALGOUSDT'),
        ('VETUSDT'),
        ('ICPUSDT'),
        ('HBARUSDT'),
        ('SANDUSDT'),
        ('MANAUSDT'),
        ('APEUSDT'),
        ('PEPEUSDT'),
        ('SHIBUSDT'),
        ('CRVUSDT'),
        ('DYDXUSDT'),
        ('FTMUSDT'),
        ('AXSUSDT'),
        ('CHZUSDT'),
        ('EGLDUSDT'),
        ('FLOWUSDT'),
        ('KAVAUSDT'),
        ('KSMUSDT'),
        ('SNXUSDT'),
        ('COMPUSDT'),
        ('ZRXUSDT')
) AS s(symbol) ON TRUE
WHERE sp.code = 'vip';

COMMIT;
