BEGIN;

-- --------------------------------------------------
-- 1. user_api_keys: fingerprints + перевірка permission-ів
-- --------------------------------------------------
ALTER TABLE user_api_keys
    ADD COLUMN IF NOT EXISTS api_key_fingerprint TEXT NULL,
    ADD COLUMN IF NOT EXISTS exchange_account_fingerprint TEXT NULL,
    ADD COLUMN IF NOT EXISTS permissions_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS permissions_checked_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS validation_status TEXT NOT NULL DEFAULT 'pending';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_user_api_keys_validation_status'
    ) THEN
        ALTER TABLE user_api_keys
        ADD CONSTRAINT chk_user_api_keys_validation_status
        CHECK (validation_status IN ('pending', 'valid', 'invalid', 'revoked'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_user_api_keys_api_key_fingerprint
    ON user_api_keys(api_key_fingerprint);

CREATE INDEX IF NOT EXISTS idx_user_api_keys_exchange_account_fingerprint
    ON user_api_keys(exchange_account_fingerprint);

CREATE INDEX IF NOT EXISTS idx_user_api_keys_validation_status
    ON user_api_keys(validation_status);

-- --------------------------------------------------
-- 2. user_subscriptions: trial-ознаки
-- --------------------------------------------------
ALTER TABLE user_subscriptions
    ADD COLUMN IF NOT EXISTS is_trial BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS trial_code TEXT NULL,
    ADD COLUMN IF NOT EXISTS ended_reason TEXT NULL;

CREATE INDEX IF NOT EXISTS idx_user_subscriptions_is_trial
    ON user_subscriptions(is_trial);

CREATE INDEX IF NOT EXISTS idx_user_subscriptions_trial_code
    ON user_subscriptions(trial_code);

-- --------------------------------------------------
-- 3. trial_consumptions
-- Один trial певного типу назавжди фіксується
-- --------------------------------------------------
CREATE TABLE IF NOT EXISTS trial_consumptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    telegram_id BIGINT NULL,
    email TEXT NULL,

    trial_code TEXT NOT NULL,

    api_key_fingerprint TEXT NULL,
    exchange_account_fingerprint TEXT NULL,

    first_ip INET NULL,
    device_fingerprint TEXT NULL,

    status TEXT NOT NULL DEFAULT 'started'
        CHECK (status IN ('started', 'completed', 'expired', 'converted', 'blocked', 'rejected')),

    rejection_reason TEXT NULL,

    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_trial_consumptions_user_trial
    ON trial_consumptions(user_id, trial_code)
    WHERE user_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_trial_consumptions_telegram_trial
    ON trial_consumptions(telegram_id, trial_code)
    WHERE telegram_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_trial_consumptions_email_trial
    ON trial_consumptions(email, trial_code)
    WHERE email IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_trial_consumptions_api_key_trial
    ON trial_consumptions(api_key_fingerprint, trial_code)
    WHERE api_key_fingerprint IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_trial_consumptions_exchange_trial
    ON trial_consumptions(exchange_account_fingerprint, trial_code)
    WHERE exchange_account_fingerprint IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_trial_consumptions_status
    ON trial_consumptions(status);

CREATE INDEX IF NOT EXISTS idx_trial_consumptions_started_at
    ON trial_consumptions(started_at);

-- --------------------------------------------------
-- 4. trial_blocklist
-- Ручний бан trial по різних ознаках
-- --------------------------------------------------
CREATE TABLE IF NOT EXISTS trial_blocklist (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    block_type TEXT NOT NULL
        CHECK (block_type IN (
            'user_id',
            'telegram_id',
            'email',
            'api_key_fingerprint',
            'exchange_account_fingerprint',
            'ip',
            'device_fingerprint'
        )),

    block_value TEXT NOT NULL,
    reason TEXT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_trial_blocklist_type_value
    ON trial_blocklist(block_type, block_value);

CREATE INDEX IF NOT EXISTS idx_trial_blocklist_expires_at
    ON trial_blocklist(expires_at);

-- --------------------------------------------------
-- 5. View: хто зараз має trial
-- --------------------------------------------------
CREATE OR REPLACE VIEW v_active_trial_users AS
SELECT
    us.id AS subscription_id,
    us.user_id,
    us.plan_id,
    us.status,
    us.starts_at,
    us.ends_at,
    us.trial_code,
    sp.code AS plan_code,
    sp.name AS plan_name,
    sp.features_json
FROM user_subscriptions us
JOIN subscription_plans sp
    ON sp.id = us.plan_id
WHERE
    us.status = 'active'
    AND us.is_trial = TRUE
    AND (us.ends_at IS NULL OR us.ends_at > NOW());

COMMIT;