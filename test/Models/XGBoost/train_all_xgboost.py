import os
import pandas as pd
import joblib
from xgboost import XGBClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report

# Таймфрейми
TIMEFRAMES = ['15m', '30m', '1h', '4h', '1d']
DATA_DIR = 'test/data/BTCUSDT'
MODEL_DIR = 'Models/XGBoost'
TARGET_COLUMN = 'target_long'

# Заборонені до використання фічі
DROP_COLUMNS = [
    'open_time', 'close_time', 'symbol',
    'open', 'high', 'low', 'close', 'volume',
    'quote_asset_volume', 'number_of_trades',
    'taker_buy_base_volume', 'taker_buy_quote_volume', 'ignore',
    'target_long', 'target_short'
]

os.makedirs(MODEL_DIR, exist_ok=True)


def train_and_save_model(timeframe: str):
    input_path = os.path.join(DATA_DIR, f'BTCUSDT_{timeframe}_with_targets.csv')

    if not os.path.exists(input_path):
        print(f"⚠️ Пропущено {timeframe} — файл не знайдено: {input_path}")
        return

    df = pd.read_csv(input_path)

    features = [col for col in df.columns if col not in DROP_COLUMNS and df[col].dtype != 'object']
    X = df[features].dropna()
    y = df.loc[X.index, TARGET_COLUMN]

    if y.nunique() < 2:
        print(f"⚠ Недостатньо класів для навчання ({timeframe})")
        return

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, shuffle=False)

    model = XGBClassifier(
        n_estimators=100,
        max_depth=6,
        learning_rate=0.1,
        use_label_encoder=False,
        eval_metric='logloss',
        verbosity=0
    )

    model.fit(X_train, y_train)
    y_pred = model.predict(X_test)

    print(f"\n🧠 [{timeframe}] Звіт точності:")
    print(classification_report(y_test, y_pred, digits=4))

    model_path = os.path.join(MODEL_DIR, f'xgb_{timeframe}.joblib')
    joblib.dump(model, model_path)

    features_path = os.path.join(MODEL_DIR, f'xgb_{timeframe}_features.txt')
    with open(features_path, 'w') as f:
        f.write('\n'.join(features))

    print(f"✅ Модель збережено → {model_path}")
    print(f"📄 Фічі збережено → {features_path}")


if __name__ == '__main__':
    for tf in TIMEFRAMES:
        train_and_save_model(tf)
