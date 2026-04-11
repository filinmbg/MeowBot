BEGIN;

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
    3650,
    '{
      "max_symbols": 10,
      "max_open_trades_total": 1,
      "max_open_trades_per_symbol": 1,
      "sandbox_enabled": true,
      "live_enabled": false
    }'::jsonb,
    TRUE,
    1
),
(
    'basic',
    'Basic',
    19.99,
    30,
    '{
      "max_symbols": 20,
      "max_open_trades_total": 5,
      "max_open_trades_per_symbol": 1,
      "sandbox_enabled": true,
      "live_enabled": true
    }'::jsonb,
    TRUE,
    2
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
      "live_enabled": true
    }'::jsonb,
    TRUE,
    3
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

-- FREE
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
WHERE sp.code = 'free'
ON CONFLICT (plan_id, symbol) DO NOTHING;

-- BASIC
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
WHERE sp.code = 'basic'
ON CONFLICT (plan_id, symbol) DO NOTHING;

-- PRO
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
        ('DYDXUSDT')
) AS s(symbol) ON TRUE
WHERE sp.code = 'pro'
ON CONFLICT (plan_id, symbol) DO NOTHING;

COMMIT;