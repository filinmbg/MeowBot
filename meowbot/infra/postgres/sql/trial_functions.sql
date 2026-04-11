BEGIN;

-- --------------------------------------------------
-- 1. Чи можна видати trial
-- --------------------------------------------------
CREATE OR REPLACE FUNCTION can_activate_trial(
    p_user_id UUID,
    p_telegram_id BIGINT,
    p_email TEXT,
    p_trial_code TEXT,
    p_api_key_fingerprint TEXT,
    p_exchange_account_fingerprint TEXT,
    p_ip TEXT DEFAULT NULL,
    p_device_fingerprint TEXT DEFAULT NULL
)
RETURNS TABLE (
    allowed BOOLEAN,
    reason TEXT
)
LANGUAGE plpgsql
AS $$
BEGIN
    -- blocklist checks
    IF EXISTS (
        SELECT 1
        FROM trial_blocklist b
        WHERE b.block_type = 'user_id'
          AND b.block_value = p_user_id::text
          AND (b.expires_at IS NULL OR b.expires_at > NOW())
    ) THEN
        RETURN QUERY SELECT FALSE, 'blocked:user_id';
        RETURN;
    END IF;

    IF p_telegram_id IS NOT NULL AND EXISTS (
        SELECT 1
        FROM trial_blocklist b
        WHERE b.block_type = 'telegram_id'
          AND b.block_value = p_telegram_id::text
          AND (b.expires_at IS NULL OR b.expires_at > NOW())
    ) THEN
        RETURN QUERY SELECT FALSE, 'blocked:telegram_id';
        RETURN;
    END IF;

    IF p_email IS NOT NULL AND EXISTS (
        SELECT 1
        FROM trial_blocklist b
        WHERE b.block_type = 'email'
          AND lower(b.block_value) = lower(p_email)
          AND (b.expires_at IS NULL OR b.expires_at > NOW())
    ) THEN
        RETURN QUERY SELECT FALSE, 'blocked:email';
        RETURN;
    END IF;

    IF p_api_key_fingerprint IS NOT NULL AND EXISTS (
        SELECT 1
        FROM trial_blocklist b
        WHERE b.block_type = 'api_key_fingerprint'
          AND b.block_value = p_api_key_fingerprint
          AND (b.expires_at IS NULL OR b.expires_at > NOW())
    ) THEN
        RETURN QUERY SELECT FALSE, 'blocked:api_key_fingerprint';
        RETURN;
    END IF;

    IF p_exchange_account_fingerprint IS NOT NULL AND EXISTS (
        SELECT 1
        FROM trial_blocklist b
        WHERE b.block_type = 'exchange_account_fingerprint'
          AND b.block_value = p_exchange_account_fingerprint
          AND (b.expires_at IS NULL OR b.expires_at > NOW())
    ) THEN
        RETURN QUERY SELECT FALSE, 'blocked:exchange_account_fingerprint';
        RETURN;
    END IF;

    IF p_ip IS NOT NULL AND EXISTS (
        SELECT 1
        FROM trial_blocklist b
        WHERE b.block_type = 'ip'
          AND b.block_value = p_ip
          AND (b.expires_at IS NULL OR b.expires_at > NOW())
    ) THEN
        RETURN QUERY SELECT FALSE, 'blocked:ip';
        RETURN;
    END IF;

    IF p_device_fingerprint IS NOT NULL AND EXISTS (
        SELECT 1
        FROM trial_blocklist b
        WHERE b.block_type = 'device_fingerprint'
          AND b.block_value = p_device_fingerprint
          AND (b.expires_at IS NULL OR b.expires_at > NOW())
    ) THEN
        RETURN QUERY SELECT FALSE, 'blocked:device_fingerprint';
        RETURN;
    END IF;

    -- previous trial checks
    IF EXISTS (
        SELECT 1
        FROM trial_consumptions tc
        WHERE tc.user_id = p_user_id
          AND tc.trial_code = p_trial_code
    ) THEN
        RETURN QUERY SELECT FALSE, 'already_used:user_id';
        RETURN;
    END IF;

    IF p_telegram_id IS NOT NULL AND EXISTS (
        SELECT 1
        FROM trial_consumptions tc
        WHERE tc.telegram_id = p_telegram_id
          AND tc.trial_code = p_trial_code
    ) THEN
        RETURN QUERY SELECT FALSE, 'already_used:telegram_id';
        RETURN;
    END IF;

    IF p_email IS NOT NULL AND EXISTS (
        SELECT 1
        FROM trial_consumptions tc
        WHERE lower(tc.email) = lower(p_email)
          AND tc.trial_code = p_trial_code
    ) THEN
        RETURN QUERY SELECT FALSE, 'already_used:email';
        RETURN;
    END IF;

    IF p_api_key_fingerprint IS NOT NULL AND EXISTS (
        SELECT 1
        FROM trial_consumptions tc
        WHERE tc.api_key_fingerprint = p_api_key_fingerprint
          AND tc.trial_code = p_trial_code
    ) THEN
        RETURN QUERY SELECT FALSE, 'already_used:api_key_fingerprint';
        RETURN;
    END IF;

    IF p_exchange_account_fingerprint IS NOT NULL AND EXISTS (
        SELECT 1
        FROM trial_consumptions tc
        WHERE tc.exchange_account_fingerprint = p_exchange_account_fingerprint
          AND tc.trial_code = p_trial_code
    ) THEN
        RETURN QUERY SELECT FALSE, 'already_used:exchange_account_fingerprint';
        RETURN;
    END IF;

    RETURN QUERY SELECT TRUE, 'ok';
END;
$$;

-- --------------------------------------------------
-- 2. Видати trial_pro
-- Перед цим у коді ти вже маєш перевірити permissions API key
-- --------------------------------------------------
CREATE OR REPLACE FUNCTION activate_trial_pro(
    p_user_id UUID,
    p_telegram_id BIGINT,
    p_email TEXT,
    p_api_key_fingerprint TEXT,
    p_exchange_account_fingerprint TEXT,
    p_ip TEXT DEFAULT NULL,
    p_device_fingerprint TEXT DEFAULT NULL
)
RETURNS TABLE (
    success BOOLEAN,
    reason TEXT
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_allowed BOOLEAN;
    v_reason TEXT;
    v_plan_id UUID;
BEGIN
    SELECT allowed, reason
    INTO v_allowed, v_reason
    FROM can_activate_trial(
        p_user_id,
        p_telegram_id,
        p_email,
        'trial_pro',
        p_api_key_fingerprint,
        p_exchange_account_fingerprint,
        p_ip,
        p_device_fingerprint
    );

    IF NOT v_allowed THEN
        INSERT INTO trial_consumptions (
            user_id,
            telegram_id,
            email,
            trial_code,
            api_key_fingerprint,
            exchange_account_fingerprint,
            first_ip,
            device_fingerprint,
            status,
            rejection_reason,
            ended_at
        )
        VALUES (
            p_user_id,
            p_telegram_id,
            p_email,
            'trial_pro',
            p_api_key_fingerprint,
            p_exchange_account_fingerprint,
            NULLIF(p_ip, '')::inet,
            p_device_fingerprint,
            'rejected',
            v_reason,
            NOW()
        )
        ON CONFLICT DO NOTHING;

        RETURN QUERY SELECT FALSE, v_reason;
        RETURN;
    END IF;

    SELECT id
    INTO v_plan_id
    FROM subscription_plans
    WHERE code = 'trial_pro'
    LIMIT 1;

    IF v_plan_id IS NULL THEN
        RETURN QUERY SELECT FALSE, 'missing_plan:trial_pro';
        RETURN;
    END IF;

    -- завершуємо всі активні trial/pending? активні free не чіпаємо, paid також не чіпаємо
    UPDATE user_subscriptions
    SET
        status = 'expired',
        ended_reason = 'replaced_by_trial_pro',
        updated_at = NOW()
    WHERE user_id = p_user_id
      AND status = 'active'
      AND is_trial = TRUE;

    INSERT INTO user_subscriptions (
        user_id,
        plan_id,
        status,
        starts_at,
        ends_at,
        auto_renew,
        cancelled_at,
        source,
        is_trial,
        trial_code,
        ended_reason
    )
    VALUES (
        p_user_id,
        v_plan_id,
        'active',
        NOW(),
        NOW() + INTERVAL '3 days',
        FALSE,
        NULL,
        'manual',
        TRUE,
        'trial_pro',
        NULL
    );

    INSERT INTO trial_consumptions (
        user_id,
        telegram_id,
        email,
        trial_code,
        api_key_fingerprint,
        exchange_account_fingerprint,
        first_ip,
        device_fingerprint,
        status
    )
    VALUES (
        p_user_id,
        p_telegram_id,
        p_email,
        'trial_pro',
        p_api_key_fingerprint,
        p_exchange_account_fingerprint,
        NULLIF(p_ip, '')::inet,
        p_device_fingerprint,
        'started'
    );

    -- ставимо live mode, але FREE як fallback збережеться окремо логікою
    UPDATE trader_settings
    SET
        trading_enabled = TRUE,
        trading_mode = 'live',
        updated_at = NOW()
    WHERE user_id = p_user_id;

    RETURN QUERY SELECT TRUE, 'trial_activated';
END;
$$;

-- --------------------------------------------------
-- 3. Завершити прострочені trial і повернути на FREE
-- --------------------------------------------------
CREATE OR REPLACE FUNCTION expire_trials_and_fallback_to_free()
RETURNS TABLE (
    affected_user_id UUID,
    action TEXT
)
LANGUAGE plpgsql
AS $$
DECLARE
    rec RECORD;
    v_free_plan_id UUID;
BEGIN
    SELECT id
    INTO v_free_plan_id
    FROM subscription_plans
    WHERE code = 'free'
    LIMIT 1;

    FOR rec IN
        SELECT us.id, us.user_id
        FROM user_subscriptions us
        WHERE us.status = 'active'
          AND us.is_trial = TRUE
          AND us.ends_at IS NOT NULL
          AND us.ends_at <= NOW()
    LOOP
        UPDATE user_subscriptions
        SET
            status = 'expired',
            ended_reason = 'trial_expired',
            updated_at = NOW()
        WHERE id = rec.id;

        UPDATE trial_consumptions
        SET
            status = 'expired',
            ended_at = NOW()
        WHERE user_id = rec.user_id
          AND trial_code = 'trial_pro'
          AND status = 'started';

        IF NOT EXISTS (
            SELECT 1
            FROM user_subscriptions us2
            WHERE us2.user_id = rec.user_id
              AND us2.status = 'active'
              AND us2.is_trial = FALSE
        ) THEN
            INSERT INTO user_subscriptions (
                user_id,
                plan_id,
                status,
                starts_at,
                ends_at,
                auto_renew,
                cancelled_at,
                source,
                is_trial,
                trial_code,
                ended_reason
            )
            VALUES (
                rec.user_id,
                v_free_plan_id,
                'active',
                NOW(),
                NULL,
                FALSE,
                NULL,
                'manual',
                FALSE,
                NULL,
                NULL
            );

            UPDATE trader_settings
            SET
                trading_mode = 'sandbox',
                updated_at = NOW()
            WHERE user_id = rec.user_id;

            RETURN QUERY SELECT rec.user_id, 'trial_expired_fallback_free';
        ELSE
            RETURN QUERY SELECT rec.user_id, 'trial_expired_paid_exists';
        END IF;
    END LOOP;
END;
$$;

COMMIT;