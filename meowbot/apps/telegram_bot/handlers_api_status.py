from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from meowbot.apps.telegram_bot.handlers_settings import render_api_screen
from meowbot.apps.telegram_bot.menu_support import (
    bool_label,
    normalize_json_mapping,
    normalize_mapping,
    safe_callback_answer,
    safe_edit_message,
)

router = Router()


class ApiKeyStates(StatesGroup):
    waiting_for_api_key = State()
    waiting_for_api_secret = State()


def _cancel_keyboard(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("api.cancel"), callback_data="api:cancel")],
        ]
    )


def _after_save_keyboard(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("reply.home"), callback_data="menu:home"),
                InlineKeyboardButton(text=t("api.status"), callback_data="api:status"),
            ],
        ]
    )


async def _resolve_lang(user, *, i18n_service, users_repo) -> tuple[dict, str]:
    row = normalize_mapping(await users_repo.get_by_telegram_id(user.id))
    lang = i18n_service.resolve_language(
        preferred_language=row.get("preferred_language") if row else None,
        telegram_language_code=getattr(user, "language_code", None),
    )
    return row, lang


async def _delete_sensitive_message(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        return


def _build_api_save_result_text(t, result: dict) -> str:
    permissions = normalize_json_mapping(result.get("permissions_json"))
    reason = str(result.get("reason") or "unknown")
    status_key = "api.save_success" if result.get("ok") else "api.save_failed"
    return "\n".join(
        [
            t(status_key),
            "",
            f"<b>{t('api.validation_reason')}:</b> <code>{reason}</code>",
            "",
            t("api.permissions_title"),
            f"• {t('api.permission_reading')}: {bool_label(t, bool(permissions.get('enableReading', False)))}",
            f"• {t('api.permission_futures')}: {bool_label(t, bool(permissions.get('enableFutures', False)))}",
            f"• {t('api.permission_withdrawals')}: {bool_label(t, bool(permissions.get('enableWithdrawals', False)))}",
        ]
    )


@router.callback_query(F.data.in_({"api:show", "api:status"}))
async def api_show_callback(callback: CallbackQuery, **deps) -> None:
    await safe_callback_answer(callback)
    await render_api_screen(callback, **deps)


@router.callback_query(F.data.in_({"api:add", "api:replace"}))
async def api_add_replace_callback(
    callback: CallbackQuery,
    state: FSMContext,
    i18n_service,
    users_repo,
) -> None:
    await safe_callback_answer(callback)
    user = callback.from_user
    if user is None:
        return
    _, lang = await _resolve_lang(user, i18n_service=i18n_service, users_repo=users_repo)
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    await state.set_state(ApiKeyStates.waiting_for_api_key)
    await state.update_data(label="main")
    await safe_edit_message(
        callback,
        text=f"{t('api.add_flow_title')}\n\n{t('api.enter_key')}\n\n{t('api.security_hint')}",
        reply_markup=_cancel_keyboard(t),
    )


@router.callback_query(F.data == "api:cancel")
async def api_cancel_callback(callback: CallbackQuery, state: FSMContext, i18n_service, users_repo, **deps) -> None:
    await safe_callback_answer(callback)
    user = callback.from_user
    if user is None:
        return
    await state.clear()
    _, lang = await _resolve_lang(user, i18n_service=i18n_service, users_repo=users_repo)
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    await safe_edit_message(callback, text=t("api.cancelled"), reply_markup=_after_save_keyboard(t))


@router.message(ApiKeyStates.waiting_for_api_key)
async def api_receive_key(
    message: Message,
    state: FSMContext,
    i18n_service,
    users_repo,
) -> None:
    user = message.from_user
    if user is None:
        return
    api_key = (message.text or "").strip()
    await _delete_sensitive_message(message)
    _, lang = await _resolve_lang(user, i18n_service=i18n_service, users_repo=users_repo)
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    if not api_key:
        await message.answer(t("api.empty_value"), parse_mode="HTML", reply_markup=_cancel_keyboard(t))
        return
    await state.update_data(api_key=api_key)
    await state.set_state(ApiKeyStates.waiting_for_api_secret)
    await message.answer(t("api.enter_secret"), parse_mode="HTML", reply_markup=_cancel_keyboard(t))


@router.message(ApiKeyStates.waiting_for_api_secret)
async def api_receive_secret(
    message: Message,
    state: FSMContext,
    save_user_binance_api_keys_usecase,
    i18n_service,
    users_repo,
) -> None:
    user = message.from_user
    if user is None:
        return
    api_secret = (message.text or "").strip()
    await _delete_sensitive_message(message)
    data = await state.get_data()
    _, lang = await _resolve_lang(user, i18n_service=i18n_service, users_repo=users_repo)
    t = lambda key, **kwargs: i18n_service.t(lang, key, **kwargs)
    if not api_secret:
        await message.answer(t("api.empty_value"), parse_mode="HTML", reply_markup=_cancel_keyboard(t))
        return

    progress = await message.answer(t("api.validating"), parse_mode="HTML")
    try:
        result = await save_user_binance_api_keys_usecase.execute(
            telegram_id=int(user.id),
            label=str(data.get("label") or "main"),
            api_key=str(data.get("api_key") or ""),
            api_secret=api_secret,
        )
        text = _build_api_save_result_text(t, result)
    except Exception as exc:
        print(f"[telegram-api] api key save failed: {type(exc).__name__}: {exc}")
        text = t("api.save_error")
    await state.clear()
    try:
        await progress.edit_text(text, parse_mode="HTML", reply_markup=_after_save_keyboard(t))
    except Exception:
        await message.answer(text, parse_mode="HTML", reply_markup=_after_save_keyboard(t))


@router.callback_query(F.data == "api:delete")
async def api_delete_callback(callback: CallbackQuery, i18n_service, users_repo) -> None:
    await safe_callback_answer(callback)
    user = callback.from_user
    if user is None:
        return
    row = await users_repo.get_by_telegram_id(user.id)
    lang = i18n_service.resolve_language(
        preferred_language=row.get("preferred_language") if row else None,
        telegram_language_code=getattr(user, "language_code", None),
    )
    await safe_callback_answer(callback, text=i18n_service.t(lang, "api.delete_hint"), show_alert=True)
