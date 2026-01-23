# configs.py

TIMEFRAMES = ["1d","4h", "1h", "30m", "15m" ]
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
# Варіанти підмножин індикаторів для тестування моделей
VARIANT_FEATURE_SETS = {
    # 2 найефективніших
    1: ["macd", "supertrend"],

    # 1 найефективний (macd) + 1-2 менш ефективних
    2: ["macd", "ema_20"],
    3: ["macd", "rsi_14"],
    4: ["macd", "bb_upper", "bb_middle", "bb_lower"],  # Bollinger
    5: ["macd", "ema_20", "rsi_14"],
    6: ["macd", "ema_20", "bb_upper", "bb_middle", "bb_lower"],

    # 3-4 топових
    7: ["macd", "supertrend", "ema_20"],
    8: ["macd", "supertrend", "rsi_14"],
    9: ["macd", "supertrend", "ema_20", "rsi_14"],

    # Додаємо ще з середніх по ефективності
    10: ["macd", "supertrend", "ema_20", "rsi_14", "bb_upper", "bb_middle", "bb_lower"],
    11: ["macd", "supertrend", "ema_20", "rsi_14", "bb_upper", "bb_middle", "bb_lower", "mfi_14"],
    12: ["macd", "supertrend", "ema_20", "rsi_14", "bb_upper", "bb_middle", "bb_lower", "mfi_14", "obv"],

    # Розширені з трендом і об'ємом
    13: ["macd", "supertrend", "ema_20", "rsi_14", "bb_upper", "bb_middle", "bb_lower", "mfi_14", "obv",
         "trend_up_valid", "trend_up_slope", "trend_up_confirmed", "trend_up_angle_deg", "trend_up_distance",
        "trend_up_break_above", "trend_up_break_below",
        "trend_down_valid", "trend_down_slope", "trend_down_confirmed", "trend_down_angle_deg",
        "trend_down_distance", "trend_down_break_above", "trend_down_break_below"],
    14: ["macd", "supertrend", "ema_20", "rsi_14", "bb_upper", "bb_middle", "bb_lower", "mfi_14", "obv",
         "trend_up_valid", "trend_up_slope", "trend_up_confirmed", "trend_up_angle_deg", "trend_up_distance",
        "trend_up_break_above", "trend_up_break_below",
        "trend_down_valid", "trend_down_slope", "trend_down_confirmed", "trend_down_angle_deg",
        "trend_down_distance", "trend_down_break_above", "trend_down_break_below",
         "pivot", "r1", "s1", "r2", "s2"],

    # Повний набір
    15: [
        "macd", "supertrend", "ema_20", "rsi_14", "bb_upper", "bb_middle", "bb_lower", "stc", "mfi_14",
        "obv", "cci_20", "mom_10", "roc_14", "zscore_20", "sar", "pivot", "r1", "s1", "r2", "s2",
        "ha_open", "ha_close",
        "trend_up_valid", "trend_up_slope", "trend_up_confirmed", "trend_up_angle_deg",
        "trend_up_distance", "trend_up_break_above", "trend_up_break_below",
        "trend_down_valid", "trend_down_slope", "trend_down_confirmed", "trend_down_angle_deg",
        "trend_down_distance", "trend_down_break_above", "trend_down_break_below"
    ]
}


