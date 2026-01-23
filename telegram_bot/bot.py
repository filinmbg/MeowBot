import os
import logging
import asyncio
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv
from telegram import Update, BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# Mongo singleton
from database.mongo.connection import init_mongo, is_connected as mongo_ok

# Binance singleton
from binance_connector.binance_conn import init_binance, is_connected as binance_ok, get_balances_nonzero

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("meowbot.tg")


@dataclass
class Settings:
    TELEGRAM_TOKEN: str
    ADMIN_CHAT_ID: Optional[int] = None
    BINANCE_API_KEY: Optional[str] = None
    BINANCE_API_SECRET: Optional[str] = None
    BINANCE_BASE_URL: Optional[str] = None  # для тестнету
    MONGO_URI: Optional[str] = None
    MONGO_DB_NAME: Optional[str] = None
    SUPABASE_URL: Optional[str] = None
    SUPABASE_API_KEY: Optional[str] = None
    WEBHOOK_URL: Optional[str] = None

    @staticmethod
    def from_env() -> "Settings":
        load_dotenv()
        token = os.getenv("TELEGRAM_TOKEN")
        if not token:
            raise RuntimeError("TELEGRAM_TOKEN не знайдено у .env")
        admin_id = os.getenv("ADMIN_CHAT_ID")
        admin_chat_id = int(admin_id) if admin_id and admin_id.isdigit() else None
        return Settings(
            TELEGRAM_TOKEN=token.strip(),
            ADMIN_CHAT_ID=admin_chat_id,
            BINANCE_API_KEY=os.getenv("BINANCE_API_KEY"),
            BINANCE_API_SECRET=os.getenv("BINANCE_API_SECRET"),
            BINANCE_BASE_URL=os.getenv("BINANCE_BASE_URL"),
            MONGO_URI=os.getenv("MONGO_URI"),
            MONGO_DB_NAME=os.getenv("MONGO_DB_NAME"),
            SUPABASE_URL=os.getenv("SUPABASE_URL"),
            SUPABASE_API_KEY=os.getenv("SUPABASE_API_KEY"),
            WEBHOOK_URL=os.getenv("WEBHOOK_URL"),
        )


SETTINGS = Settings.from_env()


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    text = (
        f"Привіт, {user.first_name or 'трейдер'}! 🐾\n\n"
        f"Команди:\n"
        f"/help — список команд\n"
        f"/ping — перевірка доступності\n"
        f"/status — стан підключень (Mongo/Binance)\n"
        f"/balance — показати баланс на Binance\n"
        f"/setwebhook <URL> — увімкнути вебхук\n"
        f"/delwebhook — вимкнути вебхук (перейти на polling)\n"
    )
    await update.message.reply_text(text)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "<b>Команди:</b>\n"
        "/start — запуск\n"
        "/help — допомога\n"
        "/ping — перевірка\n"
        "/status — статус сервісів\n"
        "/balance — баланс на Binance\n"
        "/setwebhook &lt;URL&gt; — встановити вебхук\n"
        "/delwebhook — видалити вебхук\n"
    )
    await update.message.reply_html(text)


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("pong 🏓")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    mongo_info = "MongoDB: " + ("OK" if mongo_ok() else "NOT CONNECTED")
    binance_info = "Binance: " + ("OK" if binance_ok() else "NOT CONNECTED")
    supa_info = f"Supabase: {'OK' if SETTINGS.SUPABASE_URL and SETTINGS.SUPABASE_API_KEY else 'NOT SET'}"
    text = (
        "<b>Статус підключень:</b>\n"
        f"{mongo_info}\n"
        f"{binance_info}\n"
        f"{supa_info}\n"
    )
    await update.message.reply_html(text)


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not binance_ok():
        await update.message.reply_text("❌ Binance не підключений. Перевір ключі або логи старту.")
        return
    try:
        bals = await get_balances_nonzero()
        if not bals:
            await update.message.reply_text("Баланс порожній.")
            return
        lines = [f"{a}: free={v['free']}, locked={v['locked']}" for a, v in sorted(bals.items())]
        await update.message.reply_text("💰 Баланси:\n" + "\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"⚠️ Помилка отримання балансу: {type(e).__name__}: {e}")


async def cmd_setwebhook(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Вкажи URL: /setwebhook https://<твій-домен>/tg")
        return
    url = context.args[0]
    ok = await context.bot.set_webhook(url)
    if ok:
        await update.message.reply_text(f"✅ Webhook встановлено: {url}")
    else:
        await update.message.reply_text("❌ Не вдалося встановити webhook")


async def cmd_delwebhook(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ok = await context.bot.delete_webhook(drop_pending_updates=False)
    if ok:
        await update.message.reply_text("🧹 Webhook видалено. Можна запускати polling.")
    else:
        await update.message.reply_text("❌ Не вдалося видалити webhook")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Handler error", exc_info=context.error)


async def set_bot_commands(app) -> None:
    commands = [
        BotCommand("start", "Запуск бота"),
        BotCommand("help", "Допомога"),
        BotCommand("ping", "Перевірка доступності"),
        BotCommand("status", "Статус підключень"),
        BotCommand("balance", "Баланс на Binance"),
        BotCommand("setwebhook", "Встановити webhook"),
        BotCommand("delwebhook", "Вимкнути webhook"),
    ]
    await app.bot.set_my_commands(commands)


def build_app():
    app = (
        ApplicationBuilder()
        .token(SETTINGS.TELEGRAM_TOKEN)
        .concurrent_updates(True)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("setwebhook", cmd_setwebhook))
    app.add_handler(CommandHandler("delwebhook", cmd_delwebhook))
    app.add_error_handler(on_error)
    return app


async def main() -> None:
    app = build_app()

    # 1) Mongo
    await init_mongo(
        bot=app.bot,
        admin_chat_id=SETTINGS.ADMIN_CHAT_ID,
        mongo_uri=SETTINGS.MONGO_URI,
        mongo_db_name=SETTINGS.MONGO_DB_NAME,
    )

    # 2) Binance
    await init_binance(
        bot=app.bot,
        admin_chat_id=SETTINGS.ADMIN_CHAT_ID,
        api_key=SETTINGS.BINANCE_API_KEY,
        api_secret=SETTINGS.BINANCE_API_SECRET,
        base_url=SETTINGS.BINANCE_BASE_URL,
    )

    await set_bot_commands(app)

    if SETTINGS.WEBHOOK_URL:
        log.info("Starting in WEBHOOK mode at %s", SETTINGS.WEBHOOK_URL)
        await app.bot.set_webhook(SETTINGS.WEBHOOK_URL)
        await app.start()
        await asyncio.Event().wait()
    else:
        log.info("Starting in POLLING mode")
        await app.run_polling(close_loop=False, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Bot stopped")
