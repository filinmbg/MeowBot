import asyncio
import logging
from Loader.symbols import SYMBOLS
from indicators.indicators import calculate_indicators
from database.mongo.mongo_connector import insert_trade_signal_log  # опційно
from Models.predictor import load_model_for_symbol_tf
from wallet.open_trade import open_trade  # імпорт функції відкриття трейду

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AutoTrader")

INTERVALS = ["15m", "1h", "4h", "1d"]
models = {}  # Кеш моделей

# === Завантаження всіх моделей (не лише XGBoost)
def preload_models():
    for symbol in SYMBOLS:
        for tf in INTERVALS:
            try:
                model = load_model_for_symbol_tf(symbol, tf)  # автоматично визначає тип
                models[(symbol, tf)] = model
            except FileNotFoundError:
                logger.warning(f"⛔ Модель не знайдена: {symbol}_{tf}")


# === Обробка останнього бару
async def process_entry_signal(symbol: str, interval: str, df):
    try:
        if df.empty:
            return

        df = await calculate_indicators(df)
        if df.empty:
            return

        model = models.get((symbol, interval))
        if not model:
            logger.warning(f"❌ Модель не знайдена для {symbol}_{interval}")
            return

        signal = predict_signal_with_model(df, model)  # повертає 'LONG', 'SHORT', 'HOLD'

        logger.info(f"📊 {symbol} [{interval}] → {signal}")

        # лог сигналу (не обов’язково)
        await insert_trade_signal_log(symbol, interval, signal, df.iloc[-1].to_dict())

        if signal in ["LONG", "SHORT"]:
            await open_trade(symbol, interval, signal, df.iloc[-1])  # відкриває трейд

    except Exception as e:
        logger.error(f"❌ Помилка при обробці {symbol}_{interval}: {e}")
