# configs.py

TIMEFRAMES = ["15m", "30m", "1h", "4h", "1d"]
TARGETS = ["long", "short"]

# Основні 47 індикаторних фічей, як у CSV-файлі
ALL_INDICATORS = [
    "macd", "stc", "supertrend", "rsi_14", "sma_20", "ema_20", "wma_20", "hma_20", "zl_ema_20",
    "price_from_ema_%", "ema_env", "ema_env_upper", "ema_env_lower", "sar", "stoch_rsi", "cci_20", "roc_14",
    "mom_10", "williams_r", "fisher", "obv", "ad", "mfi_14", "rvol_20", "zscore_20",
    "pivot", "r1", "s1", "r2", "s2",
    "ha_open", "ha_close",
    "trend_up_valid", "trend_up_slope", "trend_up_confirmed", "trend_up_angle_deg", "trend_up_distance",
    "trend_up_break_above", "trend_up_break_below",
    "trend_down_valid", "trend_down_slope", "trend_down_confirmed", "trend_down_angle_deg",
    "trend_down_distance", "trend_down_break_above", "trend_down_break_below", "ATR",

    # ✅ Додано Bollinger Bands
    "bb_upper", "bb_middle", "bb_lower"
]

# Варіанти підмножин індикаторів для тестування моделей
VARIANT_FEATURE_SETS = {
    1: ['rsi_14', 'macd'],
    2: ['supertrend', 'ema_20'],
    3: ['stc', 'fisher', 'zscore_20'],
    4: ['rsi_14', 'macd', 'ema_20'],
    5: ['cci_20', 'mom_10', 'zscore_20'],
    6: ['supertrend', 'sar', 'obv'],
    7: ['rsi_14', 'macd', 'ema_20', 'mfi_14'],
    8: ['supertrend', 'sar', 'obv', 'ad'],
    9: ['fisher', 'roc_14', 'zscore_20', 'stc'],
    10: ['rsi_14', 'macd', 'supertrend', 'sar', 'obv'],

    # Варіанти з повними групами
    11: ['rsi_14', 'macd', 'bb_upper', 'bb_middle', 'bb_lower'],
    12: ['supertrend', 'sar', 'ema_env', 'ema_env_upper', 'ema_env_lower'],
    13: ['macd', 'rsi_14', 'pivot', 'r1', 's1', 'r2', 's2'],
    14: ['obv', 'ad', 'ha_open', 'ha_close'],
    15: ['macd', 'trend_up_valid', 'trend_up_slope', 'trend_up_confirmed',
         'trend_down_valid', 'trend_down_slope', 'trend_down_confirmed']
}

