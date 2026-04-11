BEGIN;

DROP VIEW IF EXISTS v_user_telegram_targets CASCADE;
DROP VIEW IF EXISTS v_enabled_trading_users CASCADE;
DROP VIEW IF EXISTS v_active_user_subscriptions CASCADE;

DROP TABLE IF EXISTS admin_audit_logs CASCADE;
DROP TABLE IF EXISTS billing_webhook_events CASCADE;
DROP TABLE IF EXISTS user_api_keys CASCADE;
DROP TABLE IF EXISTS bot_sessions CASCADE;
DROP TABLE IF EXISTS payments CASCADE;
DROP TABLE IF EXISTS user_subscriptions CASCADE;
DROP TABLE IF EXISTS subscription_plan_symbols CASCADE;
DROP TABLE IF EXISTS subscription_plans CASCADE;
DROP TABLE IF EXISTS notification_preferences CASCADE;
DROP TABLE IF EXISTS trader_timeframe_settings CASCADE;
DROP TABLE IF EXISTS trader_symbol_settings CASCADE;
DROP TABLE IF EXISTS trader_settings CASCADE;
DROP TABLE IF EXISTS telegram_profiles CASCADE;
DROP TABLE IF EXISTS auth_identities CASCADE;
DROP TABLE IF EXISTS users CASCADE;

DROP FUNCTION IF EXISTS set_updated_at() CASCADE;

COMMIT;