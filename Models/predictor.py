import os
import joblib
import pandas as pd
import xgboost as xgb
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Predictor")

# === Мапінг класів
CLASS_MAP = {
    0: "SHORT",
    1: "HOLD",
    2: "LONG"
}

# === Завантаження моделі
def load_xgboost_model(symbol: str, timeframe: str) -> xgb.XGBClassifier:
    model_path = f"Models/XGBoost/{symbol}_{timeframe}.json"
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"❌ Model not found: {model_path}")

    model = xgb.XGBClassifier()
    model.load_model(model_path)
    logger.info(f"📦 Модель завантажено: {model_path}")
    return model

# === Передбачення
def predict_signal(df: pd.DataFrame, model: xgb.XGBClassifier) -> str:
    if df is None or df.empty:
        raise ValueError("❌ DataFrame порожній або None")

    row = df.iloc[-1:]
    features = [col for col in row.columns if row[col].dtype in ["float64", "int64"]]

    if not features:
        raise ValueError("❌ Не знайдено числових ознак для передбачення")

    X = row[features]
    pred = int(model.predict(X)[0])
    signal = CLASS_MAP.get(pred, "HOLD")
    logger.info(f"🧠 Сигнал моделі: {signal}")
    return signal

def load_model_for_symbol_tf(symbol: str, tf: str):
    path = f"Models/XGBoost/{symbol}_{tf}.pkl"
    if not os.path.exists(path):
        raise FileNotFoundError(f"Модель не знайдено: {path}")
    model = joblib.load(path)
    return model
