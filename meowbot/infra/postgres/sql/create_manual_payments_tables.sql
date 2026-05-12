BEGIN;

DO $$
DECLARE
    v_name TEXT;
BEGIN
    SELECT c.conname
    INTO v_name
    FROM pg_constraint c
    WHERE c.conrelid = 'user_subscriptions'::regclass
      AND c.contype = 'c'
      AND pg_get_constraintdef(c.oid) ILIKE '%status%'
    LIMIT 1;

    IF v_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE user_subscriptions DROP CONSTRAINT %I', v_name);
    END IF;
END $$;

ALTER TABLE user_subscriptions
    DROP CONSTRAINT IF EXISTS chk_user_subscriptions_status;

ALTER TABLE user_subscriptions
    ADD CONSTRAINT chk_user_subscriptions_status
    CHECK (status IN ('pending', 'active', 'expired', 'cancelled', 'failed', 'requires_api_fix'));

DO $$
DECLARE
    v_name TEXT;
BEGIN
    SELECT c.conname
    INTO v_name
    FROM pg_constraint c
    WHERE c.conrelid = 'user_subscriptions'::regclass
      AND c.contype = 'c'
      AND pg_get_constraintdef(c.oid) ILIKE '%source%'
    LIMIT 1;

    IF v_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE user_subscriptions DROP CONSTRAINT %I', v_name);
    END IF;
END $$;

ALTER TABLE user_subscriptions
    DROP CONSTRAINT IF EXISTS chk_user_subscriptions_source;

ALTER TABLE user_subscriptions
    ADD CONSTRAINT chk_user_subscriptions_source
    CHECK (source IN ('manual', 'telegram', 'site', 'admin', 'payment_webhook', 'manual_payment'));

CREATE TABLE IF NOT EXISTS promo_codes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code TEXT NOT NULL,
    discount_percent NUMERIC(5,2) NOT NULL CHECK (discount_percent >= 0 AND discount_percent <= 100),
    max_redemptions INTEGER NOT NULL CHECK (max_redemptions >= 1),
    used_redemptions INTEGER NOT NULL DEFAULT 0 CHECK (used_redemptions >= 0),
    active_from TIMESTAMPTZ NOT NULL,
    active_to TIMESTAMPTZ NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    applies_to_all_paid_plans BOOLEAN NOT NULL DEFAULT FALSE,
    specific_plan_id UUID NULL REFERENCES subscription_plans(id) ON DELETE SET NULL,
    created_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_promo_codes_code_lower
    ON promo_codes (lower(code));

CREATE INDEX IF NOT EXISTS idx_promo_codes_active_window
    ON promo_codes (is_active, active_from, active_to);

CREATE INDEX IF NOT EXISTS idx_promo_codes_specific_plan_id
    ON promo_codes (specific_plan_id);

DROP TRIGGER IF EXISTS trg_promo_codes_updated_at ON promo_codes;
CREATE TRIGGER trg_promo_codes_updated_at
BEFORE UPDATE ON promo_codes
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS purchase_intents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_code TEXT NOT NULL,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan_id UUID NOT NULL REFERENCES subscription_plans(id) ON DELETE RESTRICT,
    plan_code TEXT NOT NULL,
    payment_method_code TEXT NOT NULL,
    base_amount_usd NUMERIC(18,2) NOT NULL CHECK (base_amount_usd >= 0),
    discount_percent NUMERIC(5,2) NULL CHECK (discount_percent IS NULL OR (discount_percent >= 0 AND discount_percent <= 100)),
    discount_amount_usd NUMERIC(18,2) NULL CHECK (discount_amount_usd IS NULL OR discount_amount_usd >= 0),
    final_amount_usd NUMERIC(18,2) NOT NULL CHECK (final_amount_usd >= 0),
    promo_code_id UUID NULL REFERENCES promo_codes(id) ON DELETE SET NULL,
    status TEXT NOT NULL CHECK (status IN ('created', 'awaiting_payment', 'awaiting_manual_check', 'approved', 'rejected', 'expired', 'cancelled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata_json JSONB NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_purchase_intents_order_code
    ON purchase_intents(order_code);

CREATE INDEX IF NOT EXISTS idx_purchase_intents_user_status
    ON purchase_intents(user_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_purchase_intents_plan_status
    ON purchase_intents(plan_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_purchase_intents_expires_at
    ON purchase_intents(expires_at);

DROP TRIGGER IF EXISTS trg_purchase_intents_updated_at ON purchase_intents;
CREATE TRIGGER trg_purchase_intents_updated_at
BEFORE UPDATE ON purchase_intents
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

ALTER TABLE payments
    ADD COLUMN IF NOT EXISTS purchase_intent_id UUID NULL REFERENCES purchase_intents(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS provider_code TEXT NULL,
    ADD COLUMN IF NOT EXISTS network_code TEXT NULL,
    ADD COLUMN IF NOT EXISTS expected_amount NUMERIC(18,8) NULL CHECK (expected_amount IS NULL OR expected_amount >= 0),
    ADD COLUMN IF NOT EXISTS paid_amount NUMERIC(18,8) NULL CHECK (paid_amount IS NULL OR paid_amount >= 0),
    ADD COLUMN IF NOT EXISTS wallet_address TEXT NULL,
    ADD COLUMN IF NOT EXISTS tx_hash TEXT NULL,
    ADD COLUMN IF NOT EXISTS payment_method_code TEXT NULL;

DO $$
DECLARE
    v_name TEXT;
BEGIN
    SELECT c.conname
    INTO v_name
    FROM pg_constraint c
    WHERE c.conrelid = 'payments'::regclass
      AND c.contype = 'c'
      AND pg_get_constraintdef(c.oid) ILIKE '%status%'
    LIMIT 1;

    IF v_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE payments DROP CONSTRAINT %I', v_name);
    END IF;
END $$;

ALTER TABLE payments
    DROP CONSTRAINT IF EXISTS chk_payments_status;

ALTER TABLE payments
    ADD CONSTRAINT chk_payments_status
    CHECK (status IN ('pending', 'processing', 'success', 'failed', 'cancelled', 'refunded', 'submitted', 'succeeded', 'rejected', 'expired'));

CREATE UNIQUE INDEX IF NOT EXISTS uq_payments_purchase_intent_id
    ON payments(purchase_intent_id)
    WHERE purchase_intent_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_payments_tx_hash
    ON payments(tx_hash);

CREATE INDEX IF NOT EXISTS idx_payments_provider_code
    ON payments(provider_code);

CREATE TABLE IF NOT EXISTS manual_payment_submissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    purchase_intent_id UUID NOT NULL REFERENCES purchase_intents(id) ON DELETE CASCADE,
    payment_id UUID NOT NULL REFERENCES payments(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    submitted_tx_hash TEXT NOT NULL,
    submitted_amount NUMERIC(18,8) NULL CHECK (submitted_amount IS NULL OR submitted_amount >= 0),
    submitted_network TEXT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
    admin_reviewed_by UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    admin_reviewed_at TIMESTAMPTZ NULL,
    admin_comment TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_manual_payment_submissions_purchase_intent
    ON manual_payment_submissions(purchase_intent_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_manual_payment_submissions_payment
    ON manual_payment_submissions(payment_id);

CREATE INDEX IF NOT EXISTS idx_manual_payment_submissions_status_created_at
    ON manual_payment_submissions(status, created_at);

DROP TRIGGER IF EXISTS trg_manual_payment_submissions_updated_at ON manual_payment_submissions;
CREATE TRIGGER trg_manual_payment_submissions_updated_at
BEFORE UPDATE ON manual_payment_submissions
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS promo_code_redemptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    promo_code_id UUID NOT NULL REFERENCES promo_codes(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purchase_intent_id UUID NOT NULL REFERENCES purchase_intents(id) ON DELETE CASCADE,
    plan_id UUID NOT NULL REFERENCES subscription_plans(id) ON DELETE RESTRICT,
    discount_percent NUMERIC(5,2) NOT NULL CHECK (discount_percent >= 0 AND discount_percent <= 100),
    discount_amount NUMERIC(18,2) NOT NULL CHECK (discount_amount >= 0),
    redeemed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_promo_code_redemptions_promo_user
    ON promo_code_redemptions(promo_code_id, user_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_promo_code_redemptions_purchase_intent
    ON promo_code_redemptions(purchase_intent_id);

CREATE INDEX IF NOT EXISTS idx_promo_code_redemptions_redeemed_at
    ON promo_code_redemptions(redeemed_at DESC);

COMMIT;
