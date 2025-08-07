import os
import pandas as pd
import xgboost as xgb
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score
from joblib import dump
from tqdm import tqdm

from configs_XGB import TIMEFRAMES, TARGETS, VARIANT_FEATURE_SETS


DATA_DIR = 'test/data/BTCUSDT'
MODEL_DIR = 'Models/XGBoost/saved_models'
RESULT_CSV = 'Models/XGBoost/xgb_training_results.csv'

os.makedirs(MODEL_DIR, exist_ok=True)

results = []

for timeframe in tqdm(TIMEFRAMES, desc="Timeframes"):
    for target in TARGETS:
        file_path = os.path.join(DATA_DIR, f'BTCUSDT_{timeframe}_critical_indicators_with_targets_{target}.csv')
        if not os.path.exists(file_path):
            print(f"Файл не знайдено: {file_path}")
            continue

        df = pd.read_csv(file_path)
        if df.isnull().values.any():
            df = df.dropna()

        for variant_name, feature_list in VARIANT_FEATURE_SETS.items():
            X = df[feature_list]
            target_col = [col for col in df.columns if target in col.lower()]
            if not target_col:
                print(f"❌ Не знайдено колонку для таргету '{target}' у файлі {file_path}")
                continue
            y = df[target_col[0]]

            X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, shuffle=False)

            model = xgb.XGBClassifier(
                n_estimators=100,
                learning_rate=0.1,
                max_depth=5,
                subsample=0.8,
                colsample_bytree=0.8,
                use_label_encoder=False,
                eval_metric='logloss',
                verbosity=0
            )

            model.fit(X_train, y_train)

            y_pred = model.predict(X_test)
            acc = accuracy_score(y_test, y_pred)
            prec = precision_score(y_test, y_pred, zero_division=0)
            rec = recall_score(y_test, y_pred, zero_division=0)
            f1 = f1_score(y_test, y_pred, zero_division=0)

            model_name = f'XGB_{timeframe}_{target}_{variant_name}.joblib'
            model_path = os.path.join(MODEL_DIR, model_name)
            dump(model, model_path)

            results.append({
                'timeframe': timeframe,
                'target': target,
                'variant': variant_name,
                'accuracy': acc,
                'precision': prec,
                'recall': rec,
                'f1_score': f1,
                'model_path': model_path
            })

# Збереження результатів
results_df = pd.DataFrame(results)
results_df.to_csv(RESULT_CSV, index=False)
print(f"\nРезультати збережено в: {RESULT_CSV}")
