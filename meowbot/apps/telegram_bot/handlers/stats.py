from __future__ import annotations

import asyncio
import logging

from aiogram import Router
from aiogram.types import Message
from pymongo.errors import AutoReconnect, NetworkTimeout, PyMongoError, ServerSelectionTimeoutError

from meowbot.apps.telegram_bot.menu_support import current_trading_mode, normalize_mapping


log = logging.getLogger("meowbot")
MONGO_UI_TIMEOUT_SECONDS = 3.0
MONGO_FALLBACK_TEXT = "⚠️ Дані тимчасово недоступні. Спробуйте ще раз."


def register_stats_handlers(
    *,
    stats_uc,
    notifier,
    builder,
    admin_ids: set[str],
    runtime_repo=None,
) -> Router:
    router = Router()

    def _normalized_text(message: Message) -> str:
        return (message.text or "").strip()

    def _is_user_stats_text(message: Message) -> bool:
        text = _normalized_text(message)
        return text in {
            "/stats",
            "рџ“Љ РЎС‚Р°С‚РёСЃС‚РёРєР°",
            "РЎС‚Р°С‚РёСЃС‚РёРєР°",
            "РњРѕСЏ СЃС‚Р°С‚РёСЃС‚РёРєР°",
            "рџ“Љ РњРѕСЏ СЃС‚Р°С‚РёСЃС‚РёРєР°",
            "рџ“Љ My Stats",
            "My Stats",
            "Stats",
        }

    async def _mode_for_user(telegram_id: int) -> str:
        if runtime_repo is None:
            return "sandbox"
        runtime_row = normalize_mapping(await runtime_repo.get_by_trading_user_id(f"tg:{telegram_id}"))
        return current_trading_mode(runtime_row)

    async def _with_stats_timeout(coro, *, operation: str, user_id: str | None = None):
        try:
            return await asyncio.wait_for(coro, timeout=MONGO_UI_TIMEOUT_SECONDS)
        except (
            asyncio.TimeoutError,
            TimeoutError,
            NetworkTimeout,
            ServerSelectionTimeoutError,
            AutoReconnect,
            PyMongoError,
        ) as exc:
            log.warning(
                "[telegram-stats] mongo unavailable operation=%s user_id=%s timeout_seconds=%.1f error=%s:%s",
                operation,
                user_id,
                MONGO_UI_TIMEOUT_SECONDS,
                type(exc).__name__,
                exc,
            )
            return None

    @router.message(_is_user_stats_text)
    async def stats_handler(message: Message) -> None:
        user = message.from_user
        if user is None:
            await message.answer("Не вдалося визначити користувача.")
            return

        user_id = f"tg:{user.id}"
        mode = await _mode_for_user(user.id)
        stats = await _with_stats_timeout(
            stats_uc.get_user_stats(user_id, mode=mode),
            operation="get_user_stats",
            user_id=user_id,
        )
        if stats is None:
            await message.answer(MONGO_FALLBACK_TEXT)
            return
        text = builder.build(
            stats,
            "📊 Твоя статистика",
            "❌ У тебе ще немає трейдів",
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

        stats = await _with_stats_timeout(
            stats_uc.get_global_stats(),
            operation="get_global_stats",
            user_id=user_id,
        )
        if stats is None:
            await message.answer(MONGO_FALLBACK_TEXT)
            return
        text = builder.build(
            stats,
            "🌍 Глобальна статистика",
            "❌ Даних поки немає",
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

        stats = await _with_stats_timeout(
            stats_uc.get_symbol_stats(symbol),
            operation="get_symbol_stats",
        )
        if stats is None:
            await message.answer(MONGO_FALLBACK_TEXT)
            return
        text = builder.build(stats, f"Статистика {symbol}", "❌ Даних поки немає")

        await message.answer(text, parse_mode="HTML")

    return router
