BEGIN;

ALTER TABLE trader_settings
ALTER COLUMN max_open_trades_total DROP NOT NULL;

DELETE FROM trade_entry_cooldowns;

UPDATE trader_settings
SET
    max_open_trades_total = NULL,
    max_open_trades_per_symbol = 1,
    updated_at = now()
WHERE user_id = '9e0eb2d4-2e04-4165-99fc-ff302344b294';

COMMIT;