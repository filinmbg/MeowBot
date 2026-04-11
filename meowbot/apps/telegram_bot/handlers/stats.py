from __future__ import annotations

from aiogram import Router
from aiogram.types import Message


def register_stats_handlers(
    *,
    stats_uc,
    notifier,
    builder,
    admin_ids: set[str],
) -> Router:
    router = Router()

    def _normalized_text(message: Message) -> str:
        return (message.text or "").strip()

    def _is_user_stats_text(message: Message) -> bool:
        text = _normalized_text(message)
        return text in {
            "/stats",
            "📊 Статистика",
            "Статистика",
            "Моя статистика",
            "📊 Моя статистика",
            "📊 My Stats",
            "My Stats",
            "Stats",
        }

    @router.message(_is_user_stats_text)
    async def stats_handler(message: Message) -> None:
        user = message.from_user
        if user is None:
            await message.answer("Не вдалося визначити користувача.")
            return

        user_id = f"tg:{user.id}"
        stats = await stats_uc.get_user_stats(user_id)
        text = builder.build(
            stats,
            "📊 Твоя статистика",
            "❌ У тебе ще немає трейдів"
        )

        await message.answer(text, parse_mode="HTML")

    @router.message(lambda m: (m.text or "").strip() == "/stats_global")
    async def stats_global_handler(message: Message) -> None:
        user = message.from_user
        if user is None:
            await message.answer("Не вдалося визначити користувача.")
            return

        user_id = f"tg:{user.id}"

        if user_id not in admin_ids:
            await message.answer("⛔ Немає доступу")
            return

        stats = await stats_uc.get_global_stats()
        text = builder.build(
            stats,
            "🌍 Глобальна статистика",
            "❌ Даних поки немає"
        )

        await message.answer(text, parse_mode="HTML")

    @router.message(lambda m: (m.text or "").strip().startswith("/stats_symbol"))
    async def stats_symbol_handler(message: Message) -> None:
        text_raw = (message.text or "").strip()
        parts = text_raw.split()

        if len(parts) < 2:
            await message.answer("Формат: /stats_symbol BTCUSDT")
            return

        symbol = parts[1].upper()

        stats = await stats_uc.get_symbol_stats(symbol)
        text = builder.build(stats, f"Статистика {symbol}")

        await message.answer(text, parse_mode="HTML")

    return router