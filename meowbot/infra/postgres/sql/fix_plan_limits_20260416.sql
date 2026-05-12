-- Align subscription plan limits and symbol whitelists with runtime trading rules.
-- Safe to rerun: it rewrites plan-symbol mappings only for free/basic/pro/vip.

BEGIN;

WITH plan_limits(code, max_symbols, max_open_trades_total) AS (
    VALUES
        ('free', 10::int, 1::int),
        ('basic', 50::int, 5::int),
        ('pro', 100::int, 20::int),
        ('vip', 150::int, NULL::int)
)
UPDATE subscription_plans sp
SET
    features_json =
        COALESCE(sp.features_json, '{}'::jsonb)
        || jsonb_build_object(
            'max_symbols', pl.max_symbols,
            'max_open_trades_total', pl.max_open_trades_total,
            'max_open_trades_per_symbol', 1,
            'sandbox_enabled', true,
            'live_enabled', sp.code <> 'free'
        ),
    updated_at = NOW()
FROM plan_limits pl
WHERE sp.code = pl.code;

DELETE FROM subscription_plan_symbols
WHERE plan_id IN (
    SELECT id
    FROM subscription_plans
    WHERE code IN ('free', 'basic', 'pro', 'vip')
);

WITH central_symbols(symbol, sort_order) AS (
    VALUES
        ('BTCUSDT', 1),
        ('ETHUSDT', 2),
        ('BNBUSDT', 3),
        ('XRPUSDT', 4),
        ('ADAUSDT', 5),
        ('SOLUSDT', 6),
        ('DOGEUSDT', 7),
        ('TRXUSDT', 8),
        ('LINKUSDT', 9),
        ('LTCUSDT', 10),
        ('BCHUSDT', 11),
        ('XLMUSDT', 12),
        ('ETCUSDT', 13),
        ('ATOMUSDT', 14),
        ('FILUSDT', 15),
        ('APTUSDT', 16),
        ('ARBUSDT', 17),
        ('OPUSDT', 18),
        ('SUIUSDT', 19),
        ('NEARUSDT', 20),
        ('AVAXUSDT', 21),
        ('DOTUSDT', 22),
        ('MATICUSDT', 23),
        ('UNIUSDT', 24),
        ('AAVEUSDT', 25),
        ('ALGOUSDT', 26),
        ('VETUSDT', 27),
        ('EOSUSDT', 28),
        ('ICPUSDT', 29),
        ('SANDUSDT', 30),
        ('MANAUSDT', 31),
        ('APEUSDT', 32),
        ('RUNEUSDT', 33),
        ('GALAUSDT', 34),
        ('HBARUSDT', 35),
        ('INJUSDT', 36),
        ('SEIUSDT', 37),
        ('FTMUSDT', 38),
        ('THETAUSDT', 39),
        ('AXSUSDT', 40),
        ('CHZUSDT', 41),
        ('CRVUSDT', 42),
        ('1INCHUSDT', 43),
        ('ENJUSDT', 44),
        ('ZILUSDT', 45),
        ('KAVAUSDT', 46),
        ('WAVESUSDT', 47),
        ('COMPUSDT', 48),
        ('SNXUSDT', 49),
        ('KSMUSDT', 50),
        ('ROSEUSDT', 51),
        ('CELOUSDT', 52),
        ('LDOUSDT', 53),
        ('DYDXUSDT', 54),
        ('ARPAUSDT', 55),
        ('STXUSDT', 56),
        ('BLURUSDT', 57),
        ('JASMYUSDT', 58),
        ('GMXUSDT', 59),
        ('MASKUSDT', 60),
        ('YFIUSDT', 61),
        ('ZRXUSDT', 62),
        ('IOSTUSDT', 63),
        ('QTUMUSDT', 64),
        ('ANKRUSDT', 65),
        ('HOTUSDT', 66),
        ('ICXUSDT', 67),
        ('DASHUSDT', 68),
        ('OMGUSDT', 69),
        ('ONTUSDT', 70),
        ('SKLUSDT', 71),
        ('BATUSDT', 72),
        ('SUSHIUSDT', 73),
        ('RENUSDT', 74),
        ('RSRUSDT', 75),
        ('LRCUSDT', 76),
        ('ZENUSDT', 77),
        ('COTIUSDT', 78),
        ('STORJUSDT', 79),
        ('NKNUSDT', 80),
        ('MTLUSDT', 81),
        ('CHRUSDT', 82),
        ('DENTUSDT', 83),
        ('CELRUSDT', 84),
        ('BANDUSDT', 85),
        ('FLMUSDT', 86),
        ('TLMUSDT', 87),
        ('IDUSDT', 88),
        ('CFXUSDT', 89),
        ('HOOKUSDT', 90),
        ('MINAUSDT', 91),
        ('ASTRUSDT', 92),
        ('MAGICUSDT', 93),
        ('WOOUSDT', 94),
        ('PEOPLEUSDT', 95)
),
plan_limits(code, max_symbols) AS (
    VALUES
        ('free', 10::int),
        ('basic', 50::int),
        ('pro', 100::int),
        ('vip', 150::int)
)
INSERT INTO subscription_plan_symbols (plan_id, symbol)
SELECT sp.id, cs.symbol
FROM subscription_plans sp
JOIN plan_limits pl
    ON pl.code = sp.code
JOIN central_symbols cs
    ON cs.sort_order <= pl.max_symbols
WHERE sp.code IN ('free', 'basic', 'pro', 'vip')
ON CONFLICT (plan_id, symbol) DO NOTHING;

COMMIT;
