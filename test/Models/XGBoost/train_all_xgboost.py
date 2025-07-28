import os
import pandas as pd
import joblib
from xgboost import XGBClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report

TIMEFRAMES = ['1d', '4h', '1h', '30m', '15m']
TARGETS = ['long', 'short']
DATA_DIR = 'test/data/BTCUSDT'
MODEL_DIR = 'Models/XGBoost'

DROP_COLUMNS = [
    'open_time', 'close_time', 'symbol',
    'open', 'high', 'low', 'close', 'volume',
    'quote_asset_volume', 'number_of_trades',
    'taker_buy_base_volume', 'taker_buy_quote_volume', 'ignore',
    'target_long', 'target_short'
]

os.makedirs(MODEL_DIR, exist_ok=True)

def train_model(timeframe: str, target: str):
    target_col = f'target_{target}'
    filename = f'BTCUSDT_{timeframe}_critical_indicators_with_targets_{target}.csv'
    filepath = os.path.join(DATA_DIR, filename)

    if not os.path.exists(filepath):
        print(f"⚠️ Пропущено {timeframe}-{target} → немає файлу {filepath}")
        return

    df = pd.read_csv(filepath)
    features = [col for col in df.columns if col not in DROP_COLUMNS and df[col].dtype in [float, int]]
    X = df[features].dropna()
    y = df.loc[X.index, target_col]

    if y.nunique() < 2:
        print(f"⚠️ Недостатньо класів для {timeframe}-{target}")
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

    print(f"\n📊 Звіт {timeframe}-{target}:")
    print(classification_report(y_test, y_pred, digits=4))

    # Зберігаємо модель
    model_path = os.path.join(MODEL_DIR, f'xgb_{timeframe}_{target}.joblib')
    joblib.dump(model, model_path)

    # Зберігаємо фічі
    features_path = os.path.join(MODEL_DIR, f'xgb_{timeframe}_{target}_features.txt')
    with open(features_path, 'w') as f:
        f.write('\n'.join(features))

    print(f"✅ Модель збережено: {model_path}")
    print(f"📄 Фічі збережено: {features_path}")


if __name__ == '__main__':
    for tf in TIMEFRAMES:
        for target in TARGETS:
            train_model(tf, target)
