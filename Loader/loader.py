import asyncio
import pandas as pd
from datetime import datetime, timedelta
from binance_connector.client import get_klines, get_price
from Loader.symbols import SYMBOLS
from core.logger import logger
from database.mongo.mongo_connector import insert_if_new
from indicators.indicators import calculate_indicators
from trader.auto_trader import process_entry_signal

INTERVALS = {
    '15m': '15m',
    '1h': '1h',
    '4h': '4h',
    '1d': '1d',
}
LOOKBACK = 150  # для індикаторів

def should_load_tf(tf: str) -> bool:
    now = datetime.utcnow()
    minute = now.minute
    hour = now.hour

    if tf == "15m":
        return minute % 15 == 1
    elif tf == "1h":
        return minute == 1
    elif tf == "4h":
        return minute == 1 and hour % 4 == 0
    elif tf == "1d":
        return hour == 0 and minute == 1  # daily бар — о 00:01 UTC
    return False

def _interval_to_timedelta(interval: str) -> timedelta:
    if interval.endswith('m'):
        return timedelta(minutes=int(interval[:-1]))
    elif interval.endswith('h'):
        return timedelta(hours=int(interval[:-1]))
    elif interval.endswith('d'):
        return timedelta(days=int(interval[:-1]))
    else:
        raise ValueError(f"Unsupported interval: {interval}")

def load_closed_bars(symbol: str, interval: str) -> pd.DataFrame:
    now = datetime.utcnow()
    duration = _interval_to_timedelta(interval)
    start_time = now - duration * LOOKBACK

    raw = get_klines(symbol, interval, start_time, limit=LOOKBACK)
    if not raw:
        return pd.DataFrame()

    df = pd.DataFrame(raw, columns=[
        'open_time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'number_of_trades',
        'taker_buy_base_volume', 'taker_buy_quote_volume', 'ignore'
    ])
    df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
    df.set_index('open_time', inplace=True)
    df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].astype(float)

    # ❗️Видаляємо останній бар, якщо ще не завершився
    bar_end = df.index[-1] + duration
    if datetime.utcnow() < bar_end:
        df = df.iloc[:-1]

    return df

# 🟢 зробити асинхронною
async def fetch_current_price(symbol: str) -> float:
    return await get_price(symbol)

# 🟢 головний цикл — асинхронний
async def run_loader_loop():
    while True:
        logger.info(f"⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC — Завантаження...")

        for symbol in SYMBOLS:
            for tf_key, tf_val in INTERVALS.items():
                if not should_load_tf(tf_key):
                    continue

                df = load_closed_bars(symbol, tf_val)
                df = await calculate_indicators(df)

                if df.empty:
                    logger.warning(f"⚠️ Порожній DataFrame після індикаторів: {symbol} [{tf_val}]")
                    continue

                last_bar = df.iloc[-1:]
                logger.info(f"📊 Перевірка нового бару: {symbol} [{tf_val}] @ {last_bar.index[0]}")

                inserted = await insert_if_new(symbol, tf_val, last_bar)

                if inserted:
                    logger.info(f"✅ Новий бар збережено: {symbol} [{tf_val}] → запуск моделі")
                    await process_entry_signal(symbol, tf_val, last_bar)
                else:
                    logger.info(f"⛔️ Бар вже існує: {symbol} [{tf_val}] @ {last_bar.index[0]}")

        await asyncio.sleep(60)

# 🟢 запуск через async
async def loader_main():
    logger.info("🚀 Loader запущено")
    await run_loader_loop()

# локальний запуск
if __name__ == "__main__":
    asyncio.run(loader_main())
