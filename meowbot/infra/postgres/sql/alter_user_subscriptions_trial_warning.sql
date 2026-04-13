BEGIN;

ALTER TABLE user_subscriptions
ADD COLUMN IF NOT EXISTS trial_expiry_warning_sent_at TIMESTAMPTZ NULL;

CREATE INDEX IF NOT EXISTS idx_user_subscriptions_trial_warning
ON user_subscriptions (status, is_trial, ends_at, trial_expiry_warning_sent_at);

COMMIT;