from __future__ import annotations

DEFAULT_V1_TEST_USER_EMAILS: tuple[str, ...] = (
    "free_test@example.com",
    "basic_test@example.com",
    "pro_test@example.com",
    "vip_test@example.com",
)

DEFAULT_V2_TEST_USER_EMAILS: tuple[str, ...] = (
    "free_test_v2@example.com",
    "basic_test_v2@example.com",
    "pro_test_v2@example.com",
    "vip_test_v2@example.com",
)

DEFAULT_V1_TEST_TELEGRAM_IDS: tuple[int, ...] = (
    900001001,
    900001002,
    900001003,
    900001004,
    900011001,
    900011101,
    900011201,
    900011301,
)

DEFAULT_V2_TEST_TELEGRAM_IDS: tuple[int, ...] = (
    900012001,
    900012002,
    900012003,
    900012004,
)

_V1_TO_V2_EMAILS: dict[str, str] = dict(
    zip(DEFAULT_V1_TEST_USER_EMAILS, DEFAULT_V2_TEST_USER_EMAILS, strict=True)
)

DEFAULT_TEST_TELEGRAM_IDS: frozenset[int] = frozenset(
    (*DEFAULT_V1_TEST_TELEGRAM_IDS, *DEFAULT_V2_TEST_TELEGRAM_IDS)
)
DEFAULT_TEST_USER_EMAILS: frozenset[str] = frozenset(
    (*DEFAULT_V1_TEST_USER_EMAILS, *DEFAULT_V2_TEST_USER_EMAILS)
)


def expand_strategy_version_test_user_emails(emails: list[str] | tuple[str, ...]) -> list[str]:
    """Keep existing V1 test-user configs useful while adding matching V2 users."""

    normalized: list[str] = []
    seen: set[str] = set()
    for email in emails:
        value = str(email or "").strip().lower()
        if not value or value in seen:
            continue
        normalized.append(value)
        seen.add(value)

    if not normalized:
        normalized = [*DEFAULT_V1_TEST_USER_EMAILS]
        seen = set(normalized)

    for v1_email, v2_email in _V1_TO_V2_EMAILS.items():
        if v1_email in seen and v2_email not in seen:
            normalized.append(v2_email)
            seen.add(v2_email)

    return normalized


def normalize_strategy_plan_code(plan_code: object) -> str:
    value = str(plan_code or "unknown").strip().lower()
    if value.endswith("_v2"):
        return value[:-3]
    return value or "unknown"


def detect_strategy_version(
    *,
    strategy_version: object = None,
    plan_code: object = None,
    features_json: object = None,
    email: object = None,
    username: object = None,
    telegram_id: object = None,
) -> str:
    version = str(strategy_version or "").strip().lower()
    plan = str(plan_code or "").strip().lower()
    email_value = str(email or "").strip().lower()
    username_value = str(username or "").strip().lower()
    features = features_json if isinstance(features_json, dict) else {}
    features_version = str(features.get("strategy_version") or "").strip().lower()
    enabled_rules = features.get("enabled_strategy_rules")
    enabled_rule_values = {
        str(item or "").strip().upper()
        for item in enabled_rules
        if str(item or "").strip()
    } if isinstance(enabled_rules, (list, tuple, set)) else set()
    telegram_id_value = _int_or_none(telegram_id)

    if version == "v2":
        return "v2"
    if features_version == "v2":
        return "v2"
    if plan.endswith("_v2"):
        return "v2"
    if "LONG_BREAKOUT_V18" in enabled_rule_values:
        return "v2"
    if email_value.endswith("_v2@example.com") or "_test_v2@" in email_value:
        return "v2"
    if username_value.endswith("_v2") or username_value.endswith("_test_v2"):
        return "v2"
    if telegram_id_value in DEFAULT_V2_TEST_TELEGRAM_IDS:
        return "v2"
    return "v1"


def is_strategy_version_test_user(
    *,
    user_id: object = None,
    telegram_id: object = None,
    email: object = None,
    username: object = None,
) -> bool:
    telegram_id_value = _int_or_none(telegram_id)
    if telegram_id_value is None:
        user_id_value = str(user_id or "").strip().lower()
        if user_id_value.startswith("tg:"):
            telegram_id_value = _int_or_none(user_id_value.removeprefix("tg:"))

    if telegram_id_value in DEFAULT_TEST_TELEGRAM_IDS:
        return True

    email_value = str(email or "").strip().lower()
    if email_value in DEFAULT_TEST_USER_EMAILS:
        return True
    if email_value.endswith("_test@example.com") or email_value.endswith("_test_v2@example.com"):
        return True

    username_value = str(username or "").strip().lower()
    if username_value in {
        "free_test_user",
        "basic_test_user",
        "pro_test_user",
        "vip_test_user",
        "free_test_v2",
        "basic_test_v2",
        "pro_test_v2",
        "vip_test_v2",
    }:
        return True
    if username_value.endswith("_test_user") or username_value.endswith("_test_v2"):
        return True

    return False


def _int_or_none(value: object) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
