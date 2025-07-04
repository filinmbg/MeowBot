import pandas as pd
from datetime import datetime, timedelta
from Loader.symbols import SYMBOLS
from binance_connector.client import get_klines
from indicators.indicators import calculate_indicators
import logging
from database.mongo.mongo_connector import count_bars, insert_if_new, delete_bars

# === Конфіг
REQUIRED_BARS = 100
LOOKBACK_MARGIN = 250  # запас для індикаторів
INTERVALS = {
    '15m': '15m',
    '1h': '1h',
    '4h': '4h',
    '1d': '1d',
}

# === Логер
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MongoCheck")


# === Інструменти

def interval_to_timedelta(interval: str) -> timedelta:
    if interval.endswith('m'):
        return timedelta(minutes=int(interval[:-1]))
    elif interval.endswith('h'):
        return timedelta(hours=int(interval[:-1]))
    elif interval.endswith('d'):
        return timedelta(days=int(interval[:-1]))
    raise ValueError(f"⛔ Unsupported interval format: {interval}")


def prepare_dataframe(raw: list, interval: str) -> pd.DataFrame:
    if not isinstance(raw, list) or len(raw) == 0:
        raise ValueError("❌ Empty or invalid raw data")

    df = pd.DataFrame(raw, columns=[
        'open_time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'number_of_trades',
        'taker_buy_base_volume', 'taker_buy_quote_volume', 'ignore'
    ])
    df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
    df.set_index('open_time', inplace=True)

    df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].astype(float)

    # Видаляємо останній бар, якщо він ще не завершений
    last_bar_time = df.index[-1]
    bar_end = last_bar_time + interval_to_timedelta(interval)
    if datetime.utcnow() < bar_end:
        df = df.iloc[:-1]

    return df


def download_missing_bars(symbol: str, interval: str, needed_bars: int) -> pd.DataFrame:
    total_needed = needed_bars + LOOKBACK_MARGIN
    duration = interval_to_timedelta(interval)
    start_time = datetime.utcnow() - duration * total_needed

    logger.info(f"📥 Завантаження {total_needed} барів для {symbol} [{interval}]...")
    raw = get_klines(symbol, interval, start_time, limit=total_needed)
    df = prepare_dataframe(raw, interval)
    return df


async def save_to_mongo(symbol: str, interval: str, df: pd.DataFrame):
    if df.empty:
        logger.warning(f"⚠️ Порожній DataFrame для {symbol} [{interval}], не зберігається.")
        return

    try:
        df = await calculate_indicators(df)

        if df.empty:
            logger.warning(f"⚠️ Порожній DataFrame після обчислення індикаторів")
            return

        records = df.reset_index().to_dict(orient="records")

        # === ВАЖЛИВО: очищення барів для 1d
        if interval == '1d':
            await delete_bars(symbol, interval)

        await insert_if_new(symbol, interval, df)

    except Exception as e:
        logger.error(f"❌ Помилка при збереженні в Mongo: {symbol} [{interval}]: {e}")


async def check_symbol_tf(symbol: str, interval: str):
    try:
        current_count = await count_bars(symbol, interval)
        logger.info(f"{symbol} [{interval}] має {current_count} барів у Mongo")

        if current_count >= REQUIRED_BARS:
            return True

        missing = REQUIRED_BARS - current_count
        logger.warning(f"⛔ Бракує {missing} барів — дозавантаження {symbol} [{interval}]")

        df = download_missing_bars(symbol, interval, missing)
        await save_to_mongo(symbol, interval, df)

        # Повторна перевірка
        final_count = await count_bars(symbol, interval)
        if final_count >= REQUIRED_BARS:
            logger.info(f"✅ {symbol} [{interval}] тепер має {final_count} барів")
            return True
        else:
            logger.error(f"❌ Після дозавантаження лише {final_count} барів для {symbol} [{interval}]")
            return False

    except Exception as e:
        logger.exception(f"❌ Перевірка зірвалась для {symbol} [{interval}]: {e}")
        return False


# === Головна перевірка

async def run_check():
    logger.info("🚦 Перевірка наявності ≥100 барів з індикаторами в MongoDB...")
    all_ok = True
    for symbol in SYMBOLS:
        for tf in INTERVALS:
            result = await check_symbol_tf(symbol, tf)
            if not result:
                all_ok = False

    if not all_ok:
        logger.error("❌ Не всі символи/таймфрейми готові. Зупинка.")
        raise SystemExit(1)

    logger.info("✅ Mongo готова. Можна запускати loader.py")

if __name__ == "__main__":
    run_check()
