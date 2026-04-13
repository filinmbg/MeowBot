from __future__ import annotations

from aiogram import Router
from aiogram.types import Message


def register_api_keys_handlers(
    *,
    save_user_binance_api_keys_uc,
    user_api_keys_repo,
    telegram_user_account_repo,
) -> Router:
    router = Router()

    @router.message(lambda m: (m.text or "").strip().startswith("/set_binance_keys"))
    async def set_binance_keys_handler(message: Message) -> None:
        user = message.from_user
        if user is None:
            await message.answer("Не вдалося визначити користувача.")
            return

        text = (message.text or "").strip()
        parts = text.split(maxsplit=3)

        if len(parts) < 3:
            await message.answer(
                "Формат:\n"
                "<code>/set_binance_keys API_KEY API_SECRET</code>\n\n"
                "Або:\n"
                "<code>/set_binance_keys mylabel API_KEY API_SECRET</code>",
                parse_mode="HTML",
            )
            return

        if len(parts) == 3:
            label = "main"
            api_key = parts[1].strip()
            api_secret = parts[2].strip()
        else:
            label = parts[1].strip()
            api_key = parts[2].strip()
            api_secret = parts[3].strip()

        result = await save_user_binance_api_keys_uc.execute(
            telegram_id=int(user.id),
            label=label,
            api_key=api_key,
            api_secret=api_secret,
        )

        await message.answer(result["message"], parse_mode="HTML")

    @router.message(lambda m: (m.text or "").strip() in {"/my_api_key_status", "API статус", "🔑 API статус"})
    async def my_api_key_status_handler(message: Message) -> None:
        user = message.from_user
        if user is None:
            await message.answer("Не вдалося визначити користувача.")
            return

        account = await telegram_user_account_repo.get_user_by_telegram_id(int(user.id))
        if not account:
            await message.answer("Користувача не знайдено в Postgres.")
            return

        key_row = await user_api_keys_repo.get_active_key_by_runtime_user_id(
            runtime_user_id=f"tg:{user.id}",
            exchange="binance",
        )

        if not key_row:
            await message.answer("🔒 Активного Binance API ключа не знайдено.")
            return

        permissions = key_row.get("permissions_json") or {}
        enable_reading = bool(permissions.get("enableReading", False))
        enable_futures = bool(permissions.get("enableFutures", False))
        enable_withdrawals = bool(permissions.get("enableWithdrawals", False))

        text = "\n".join(
            [
                "🔑 Статус Binance API ключа",
                f"Label: <code>{key_row.get('label')}</code>",
                f"Validation: <code>{key_row.get('validation_status')}</code>",
                f"Is active: <code>{key_row.get('is_active')}</code>",
                "",
                "Permissions:",
                f"• Reading: <code>{enable_reading}</code>",
                f"• Futures: <code>{enable_futures}</code>",
                f"• Withdrawals: <code>{enable_withdrawals}</code>",
            ]
        )
        await message.answer(text, parse_mode="HTML")

    return router