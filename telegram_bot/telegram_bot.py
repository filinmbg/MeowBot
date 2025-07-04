import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.types import Message
from aiogram.filters import CommandStart
from aiogram.client.default import DefaultBotProperties
from core.logger import logger
from database.mongo.user_manager import get_or_create_user

from dotenv import load_dotenv
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

bot = Bot(
    token=TELEGRAM_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

@dp.message(CommandStart())
async def start_handler(message: Message):
    try:
        tg_user = message.from_user.model_dump()
        user = get_or_create_user(tg_user)

        text = (
            f"👋 Привіт, @{user['username']}!\n"
            f"🆔 Telegram ID: {user['telegram_id']}\n"
            f"🔐 Роль: {user['role']}\n"
            f"📦 Підписка: {user['subscription']}\n"
            f"💰 Баланс: {user['wallet']['balance']} {user['wallet']['currency']}"
        )
        await message.answer(text)
    except Exception as e:
        logger.error(f"❌ Помилка: {e}")
        await message.answer("🚫 Помилка при реєстрації. Спробуй ще раз.")

async def main():
    logger.info("🤖 Telegram бот запущено")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
