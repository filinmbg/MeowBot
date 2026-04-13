BEGIN;

ALTER TABLE trader_settings
ADD COLUMN IF NOT EXISTS max_margin_per_trade_mode TEXT DEFAULT 'percent';

ALTER TABLE trader_settings
ADD COLUMN IF NOT EXISTS max_margin_per_trade_value DOUBLE PRECISION DEFAULT 5.0;

ALTER TABLE trader_settings
ADD COLUMN IF NOT EXISTS margin_ratio_warn_pct DOUBLE PRECISION DEFAULT 6.0;

ALTER TABLE trader_settings
ADD COLUMN IF NOT EXISTS margin_ratio_block_pct DOUBLE PRECISION DEFAULT 10.0;

COMMIT;