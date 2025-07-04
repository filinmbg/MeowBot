import pandas as pd
import pandas_ta as ta
from utils.semaphore import GlobalSemaphore
import logging

logger = logging.getLogger("Indicators")
semaphore = GlobalSemaphore(max_concurrent=5)

# === Валідація
def validate_dataframe(df: pd.DataFrame):
    required = ['open', 'high', 'low', 'close', 'volume']
    for col in required:
        if col not in df.columns:
            raise ValueError(f"❌ Missing column: {col}")

# === Індикатори по групах
def add_oscillators(df: pd.DataFrame):
    df.ta.rsi(length=14, append=True)
    df.ta.stoch(length=14, append=True)
    df.ta.cci(length=20, append=True)
    willr = ta.willr(df['high'], df['low'], df['close'], length=14)
    if willr is not None:
        df.loc[:, 'williams_r'] = willr
    df.loc[:, 'momentum'] = df['close'] - df['close'].shift(10)

def add_trend_indicators(df: pd.DataFrame):
    df.ta.sma(length=20, append=True)
    df.ta.ema(length=12, append=True)
    df.ta.ema(length=26, append=True)
    df.ta.ema(length=50, append=True)
    df.ta.wma(length=20, append=True)
    df.ta.macd(append=True)
    df.ta.trix(append=True)
    df.ta.adx(length=14, append=True)

def add_volatility_indicators(df: pd.DataFrame):
    df.ta.atr(length=14, append=True)
    df.ta.bbands(length=20, append=True)
    df.ta.donchian(length=20, append=True)
    df.ta.kc(length=20, append=True)

def add_volume_indicators(df: pd.DataFrame):
    df.loc[:, 'volume_sma_20'] = df['volume'].rolling(20).mean()
    obv = df.ta.obv()
    if obv is not None:
        df.loc[:, 'OBV'] = obv
    df.loc[:, 'force_index'] = (df['close'] - df['close'].shift(1)) * df['volume']

def add_misc_indicators(df: pd.DataFrame):
    df.loc[:, 'elder_bull'] = df['high'] - df['close'].ewm(span=13).mean()
    df.loc[:, 'elder_bear'] = df['low'] - df['close'].ewm(span=13).mean()

def add_ichimoku_cloud(df: pd.DataFrame):
    try:
        ichi_df, _ = ta.ichimoku(df['high'], df['low'], df['close'], offset=0)
        if ichi_df is not None:
            for col in ichi_df.columns:
                df.loc[:, col] = ichi_df[col]
    except Exception as e:
        logger.warning(f"⚠️ Ichimoku не розраховано: {e}")

# === Основна функція
async def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    async def _task():
        try:
            df = df.copy()  # 🟢 важливо! захищаємося від SettingWithCopyWarning
            validate_dataframe(df)
            add_oscillators(df)
            add_trend_indicators(df)
            add_volatility_indicators(df)
            add_volume_indicators(df)
            add_misc_indicators(df)
            add_ichimoku_cloud(df)

            df.dropna(inplace=True)
            logger.info(f"✅ Індикатори розраховані для {len(df)} барів")
            if df.empty:
                logger.warning("⚠️ Порожній DataFrame після обчислення індикаторів")
            return df
        except Exception as e:
            logger.error(f"❌ Помилка в індикаторах: {e}")
            return pd.DataFrame()

    return await semaphore.run(_task)