from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from meowbot.apps.telegram_bot.handlers_settings import render_risk_screen
from meowbot.apps.telegram_bot.keyboards.risk_menu import (
    build_risk_parameter_keyboard,
    build_risk_saved_keyboard,
    build_stake_mode_keyboard,
)
from meowbot.apps.telegram_bot.menu_support import (
    html_value,
    normalize_mapping,
    safe_callback_answer,
    safe_edit_message,
)


router = Router()


class RiskEditStates(StatesGroup):
    waiting_for_stake_value = State()
    waiting_for_leverage = State()
    waiting_for_margin_limit = State()
    waiting_for_warn_threshold = State()
    waiting_for_block_threshold = State()
    waiting_for_cooldown = State()
    waiting_for_risk_trades_limit = State()
    waiting_for_sandbox_start_balance = State()


PARAMETER_STATES = {
    "stake_value": RiskEditStates.waiting_for_stake_value,
    "leverage": RiskEditStates.waiting_for_leverage,
    "margin_limit": RiskEditStates.waiting_for_margin_limit,
    "warn_threshold": RiskEditStates.waiting_for_warn_threshold,
    "block_threshold": RiskEditStates.waiting_for_block_threshold,
    "cooldown": RiskEditStates.waiting_for_cooldown,
    "risk_trades_limit": RiskEditStates.waiting_for_risk_trades_limit,
    "sandbox_start_balance": RiskEditStates.waiting_for_sandbox_start_balance,
}

STATE_PARAMETERS = {
    RiskEditStates.waiting_for_stake_value.state: "stake_value",
    RiskEditStates.waiting_for_leverage.state: "leverage",
    RiskEditStates.waiting_for_margin_limit.state: "margin_limit",
    RiskEditStates.waiting_for_warn_threshold.state: "warn_threshold",
    RiskEditStates.waiting_for_block_threshold.state: "block_threshold",
    RiskEditStates.waiting_for_cooldown.state: "cooldown",
    RiskEditStates.waiting_for_risk_trades_limit.state: "risk_trades_limit",
    RiskEditStates.waiting_for_sandbox_start_balance.state: "sandbox_start_balance",
}


async def _context(user, *, i18n_service, users_repo, runtime_repo):
    user_row = normalize_mapping(await users_repo.get_by_telegram_id(user.id))
    lang = i18n_service.resolve_language(
        preferred_language=user_row.get("preferred_language") if user_row else None,
        telegram_language_code=getattr(user, "language_code", None),
    )
    runtime_row = normalize_mapping(await runtime_repo.get_by_trading_user_id(f"tg:{user.id}"))
    return user_row, runtime_row, lang


def _stake_mode_key(value: str | None) -> str:
    return "risk.mode.percent" if str(value or "percent").lower() == "percent" else "risk.mode.fixed"


def _current_value(t, runtime_row: dict, parameter: str) -> str:
    if parameter == "stake_mode":
        return t(_stake_mode_key(str(runtime_row.get("default_stake_mode") or "percent")))
    if parameter == "stake_value":
        mode = str(runtime_row.get("default_stake_mode") or "percent")
        suffix = "%" if mode == "percent" else "USDT"
        return f"{runtime_row.get('default_stake_value', '-')}{suffix}"
    if parameter == "leverage":
        return f"x{int(runtime_row.get('default_leverage', 0) or 0)}"
    if parameter == "margin_limit":
        mode = str(runtime_row.get("max_margin_per_trade_mode") or "percent")
        suffix = "%" if mode == "percent" else "USDT"
        return f"{runtime_row.get('max_margin_per_trade_value', '-')}{suffix}"
    if parameter == "warn_threshold":
        return f"{runtime_row.get('margin_ratio_warn_pct', '-')}%"
    if parameter == "block_threshold":
        return f"{runtime_row.get('margin_ratio_block_pct', '-')}%"
    if parameter == "cooldown":
        enabled = bool(runtime_row.get("loss_cooldown_enabled", False))
        minutes = runtime_row.get("loss_cooldown_minutes", "-")
        status = t("common.enabled") if enabled else t("common.disabled")
        return f"{minutes} {t('risk.minutes_short')} ({status})"
    if parameter == "risk_trades_limit":
        return str(runtime_row.get("max_risk_trades", 5))
    if parameter == "sandbox_start_balance":
        return f"{runtime_row.get('sandbox_start_balance_usd', 1000.0)} USD"
    return "-"


def _parse_decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(",", ".").strip())
    except (InvalidOperation, AttributeError):
        return None


def _validate_value(parameter: str, value: str, runtime_row: dict):
    if parameter == "stake_value":
        parsed = _parse_decimal(value)
        mode = str(runtime_row.get("default_stake_mode") or "percent")
        if parsed is None or parsed <= 0:
            return None, "risk.invalid_positive_number"
        if mode == "percent" and parsed > 100:
            return None, "risk.invalid_stake_percent"
        return parsed, None

    if parameter == "leverage":
        parsed = _parse_decimal(value)
        if parsed is None or parsed != parsed.to_integral_value() or parsed < 1 or parsed > 125:
            return None, "risk.invalid_leverage"
        return int(parsed), None

    if parameter == "margin_limit":
        parsed = _parse_decimal(value)
        if parsed is None or parsed <= 0:
            return None, "risk.invalid_positive_number"
        return parsed, None

    if parameter == "warn_threshold":
        parsed = _parse_decimal(value)
        block = _parse_decimal(str(runtime_row.get("margin_ratio_block_pct") or "0"))
        if parsed is None or parsed <= 0 or parsed > 100:
            return None, "risk.invalid_percent"
        if block is not None and block > 0 and parsed >= block:
            return None, "risk.invalid_warn_threshold"
        return parsed, None

    if parameter == "block_threshold":
        parsed = _parse_decimal(value)
        warn = _parse_decimal(str(runtime_row.get("margin_ratio_warn_pct") or "0"))
        if parsed is None or parsed <= 0 or parsed > 100:
            return None, "risk.invalid_percent"
        if warn is not None and parsed <= warn:
            return None, "risk.invalid_block_threshold"
        return parsed, None

    if parameter == "cooldown":
        parsed = _parse_decimal(value)
        if parsed is None or parsed != parsed.to_integral_value() or parsed < 0 or parsed > 10080:
            return None, "risk.invalid_cooldown"
        return int(parsed), None

    if parameter == "risk_trades_limit":
        parsed = _parse_decimal(value)
        if parsed is None or parsed != parsed.to_integral_value() or parsed < 1 or parsed > 100:
            return None, "risk.invalid_risk_trades_limit"
        return int(parsed), None

    if parameter == "sandbox_start_balance":
        parsed = _parse_decimal(value)
        if parsed is None or parsed <= 0 or parsed > Decimal("10000000"):
            return None, "risk.invalid_sandbox_start_balance"
        return parsed, None

    return None, "risk.invalid_value"


async def render_risk_parameter_screen(
    target,
    *,
    parameter: str,
    i18n_service,
    users_repo,
    runtime_repo,
    **_,
) -> None:
    user = target.from_user
    if user is None:
        return
    _, runtime_row, lang = await _context(
        user,
        i18n_service=i18n_service,
        users_repo=users_repo,
        runtime_repo=runtime_repo,
    )
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    text = "\n".join(
        [
            t(f"risk.param.{parameter}.title"),
            "",
            t(f"risk.param.{parameter}.description"),
            "",
            f"<b>{t('risk.current_value')}:</b> {html_value(_current_value(t, runtime_row, parameter))}",
            "",
            t(f"risk.param.{parameter}.warning"),
        ]
    )
    if parameter == "stake_mode":
        await safe_edit_message(
            target,
            text=text,
            reply_markup=build_stake_mode_keyboard(t),
        )
        return

    await safe_edit_message(
        target,
        text=text,
        reply_markup=build_risk_parameter_keyboard(t, parameter=parameter),
    )


@router.callback_query(F.data == "risk:show")
async def risk_show_callback(callback: CallbackQuery, state: FSMContext, **deps) -> None:
    await safe_callback_answer(callback)
    await state.clear()
    await render_risk_screen(callback, **deps)


@router.callback_query(
    F.data.in_(
        {
            "risk:leverage",
            "risk:stake_mode",
            "risk:stake_value",
            "risk:margin_limit",
            "risk:warn_threshold",
            "risk:block_threshold",
            "risk:cooldown",
            "risk:risk_trades_limit",
            "risk:sandbox_start_balance",
        }
    )
)
async def risk_parameter_callback(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    parameter = str(callback.data or "").split(":", 1)[-1]
    await render_risk_parameter_screen(callback, parameter=parameter, **deps)


@router.callback_query(F.data.startswith("risk:stake_mode:set:"))
async def risk_stake_mode_set_callback(
    callback: CallbackQuery,
    state: FSMContext,
    i18n_service,
    users_repo,
    runtime_repo,
    trader_settings_repo,
    **deps,
) -> None:
    await safe_callback_answer(callback)
    user = callback.from_user
    if user is None:
        return
    mode = callback.data.rsplit(":", 1)[-1]
    if mode not in {"percent", "fixed"}:
        await safe_callback_answer(callback)
        return
    user_row, _, lang = await _context(
        user,
        i18n_service=i18n_service,
        users_repo=users_repo,
        runtime_repo=runtime_repo,
    )
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    if not user_row:
        await safe_callback_answer(callback, text=t("payment.error_user_not_found"), show_alert=True)
        await state.clear()
        return

    await trader_settings_repo.update_risk_parameter_by_user_id(
        user_row["id"],
        parameter="stake_mode",
        value=mode,
    )
    await state.clear()
    next_deps = dict(deps)
    next_deps.update(
        {
            "i18n_service": i18n_service,
            "users_repo": users_repo,
            "runtime_repo": runtime_repo,
            "trader_settings_repo": trader_settings_repo,
        }
    )
    await render_risk_parameter_screen(callback, parameter="stake_mode", **next_deps)


@router.callback_query(F.data.startswith("risk:edit:"))
async def risk_edit_callback(
    callback: CallbackQuery,
    state: FSMContext,
    i18n_service,
    users_repo,
    runtime_repo,
) -> None:
    await safe_callback_answer(callback)
    user = callback.from_user
    if user is None:
        return
    parameter = callback.data.rsplit(":", 1)[-1]
    next_state = PARAMETER_STATES.get(parameter)
    if next_state is None:
        await safe_callback_answer(callback)
        return
    _, _, lang = await _context(
        user,
        i18n_service=i18n_service,
        users_repo=users_repo,
        runtime_repo=runtime_repo,
    )
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    await state.set_state(next_state)
    await state.update_data(parameter=parameter)
    await safe_edit_message(
        callback,
        text=t(f"risk.param.{parameter}.prompt"),
        reply_markup=build_risk_parameter_keyboard(t, parameter=parameter),
    )


@router.message(RiskEditStates.waiting_for_leverage)
@router.message(RiskEditStates.waiting_for_stake_value)
@router.message(RiskEditStates.waiting_for_margin_limit)
@router.message(RiskEditStates.waiting_for_warn_threshold)
@router.message(RiskEditStates.waiting_for_block_threshold)
@router.message(RiskEditStates.waiting_for_cooldown)
@router.message(RiskEditStates.waiting_for_risk_trades_limit)
@router.message(RiskEditStates.waiting_for_sandbox_start_balance)
async def risk_receive_value(
    message: Message,
    state: FSMContext,
    i18n_service,
    users_repo,
    runtime_repo,
    trader_settings_repo,
) -> None:
    user = message.from_user
    if user is None:
        return
    current_state = await state.get_state()
    parameter = STATE_PARAMETERS.get(str(current_state), "")
    user_row, runtime_row, lang = await _context(
        user,
        i18n_service=i18n_service,
        users_repo=users_repo,
        runtime_repo=runtime_repo,
    )
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    value, error_key = _validate_value(parameter, message.text or "", runtime_row)
    if error_key:
        await message.answer(t(error_key), parse_mode="HTML")
        return
    if not user_row:
        await message.answer(t("payment.error_user_not_found"), parse_mode="HTML")
        await state.clear()
        return

    await trader_settings_repo.update_risk_parameter_by_user_id(
        user_row["id"],
        parameter=parameter,
        value=value,
    )
    await state.clear()
    await message.answer(
        t("risk.saved"),
        parse_mode="HTML",
        reply_markup=build_risk_saved_keyboard(t),
    )
