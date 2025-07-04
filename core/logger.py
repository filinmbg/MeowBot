import logging
import os
import asyncio
from aiogram import Bot
from config.admin import ADMIN_TELEGRAM_ID
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
bot = Bot(token=BOT_TOKEN)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MeowLogger")

async def send_admin_alert_async(message: str):
    try:
        await bot.send_message(ADMIN_TELEGRAM_ID, f"⚠️ <b>Alert:</b>\n{message}", parse_mode="HTML")
    except Exception as e:
        logger.error(f"❌ Failed to send alert to admin: {e}")

def send_admin_alert(message: str):
    try:
        asyncio.get_event_loop().create_task(send_admin_alert_async(message))
    except RuntimeError:
        asyncio.run(send_admin_alert_async(message))
