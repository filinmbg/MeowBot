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
    "trend_down_distance", "trend_down_break_above", "trend_down_break_below", "ATR"
]

# Варіанти підмножин індикаторів для тестування моделей
VARIANT_FEATURE_SETS = {
    "V1": ["macd", "stc"],
    "V2": ["macd", "stc", "supertrend"],
    "V3": ["macd", "stc", "supertrend", "rsi_14"],
    "V4": ["macd", "stc", "supertrend", "rsi_14", "sma_20"],
    "V5": ["macd", "stc", "supertrend", "rsi_14", "sma_20", "sar"],
    "V6": ["macd", "stc", "supertrend", "rsi_14", "sma_20", "sar", "mom_10"],
    "V7": ["macd", "stc", "supertrend", "rsi_14", "sma_20", "sar", "mom_10", "obv"],
    "V8": ["macd", "stc", "supertrend", "rsi_14", "sma_20", "sar", "mom_10", "obv", "trend_up_slope"],
    "V9": ["macd", "stc", "supertrend", "rsi_14", "sma_20", "sar", "mom_10", "obv", "trend_up_slope", "ATR"],
    "V10": ALL_INDICATORS
}
