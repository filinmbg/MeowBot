import pandas as pd
import xgboost as xgb
import os
from glob import glob
from datetime import timedelta

# === Налаштування ===
symbol = 'BTCUSDT'
base_path = f'test/data/{symbol}'
model_dir = 'Models/XGBoost'
result_dir = 'results'
os.makedirs(result_dir, exist_ok=True)

initial_balance = 1000
entry_pct = 0.01
leverage = 20
max_exit_bars = 240  # ≈ 4 години

# === Завантаження 1m барів ===
df_1m = pd.read_parquet(f'{base_path}/{symbol}_1m_labeled.parquet').dropna()

# === Завантаження моделей ===
models = sorted(glob(f'{model_dir}/{symbol}_*.json'))

for model_path in models:
    tf = model_path.split('_')[-1].replace('.json', '')
    data_path = f'{base_path}/{symbol}_{tf}_labeled.parquet'
    if not os.path.exists(data_path):
        print(f"⚠️ Пропущено: {data_path} не знайдено")
        continue

    print(f"\n🔍 Тестування моделі: {tf}")
    df_tf = pd.read_parquet(data_path).dropna()

    # — Останній рік
    one_year_ago = df_tf.index[-1] - timedelta(days=365)
    df_tf = df_tf[df_tf.index >= one_year_ago].copy()

    if len(df_tf) < 50:
        print(f"⚠️ Недостатньо даних для {tf}, пропущено...")
        continue

    # === Модель
    model = xgb.XGBClassifier()
    model.load_model(model_path)

    drop_cols = ['open_time', 'symbol']
    features = [col for col in df_tf.columns if col not in drop_cols and df_tf[col].dtype in ['float64', 'int64']]

    balance = initial_balance
    trades = []

    for i in range(len(df_tf) - 1):
        progress = int((i / len(df_tf)) * 100)
        if i % max(1, len(df_tf) // 20) == 0:
            print(f"⏳ {progress}% виконано — {i}/{len(df_tf)}")

        row = df_tf.iloc[i]
        X = row[features].values.reshape(1, -1)
        pred = int(model.predict(X)[0])  # 0=SHORT, 1=HOLD, 2=LONG

        if pred == 1:
            continue

        entry_time = df_tf.index[i]
        entry_price = row['close']
        position = 'LONG' if pred == 2 else 'SHORT'
        direction = 1 if position == 'LONG' else -1

        position_size = balance * entry_pct * leverage
        partial_size = position_size * 0.25
        stop = None
        exited = 0.0
        realized_pnl = 0.0

        # === Вихід: 1m бари після entry_time (обмежено)
        df_exit = df_1m[df_1m.index >= entry_time].head(max_exit_bars)

        print(f"📅 {tf} | Entry: {entry_time} | Барів для виходу: {len(df_exit)}")

        for _, row_1m in df_exit.iterrows():
            current_price = row_1m['close']
            change = direction * (current_price - entry_price) / entry_price

            # === Часткові виходи
            if exited < 0.25 and change >= 0.005:
                realized_pnl += partial_size * change
                exited += 0.25
                stop = 0.0
            elif exited < 0.5 and change >= 0.01:
                realized_pnl += partial_size * change
                exited += 0.25
                stop = 0.005
            elif exited < 0.75 and change >= 0.015:
                realized_pnl += partial_size * change
                exited += 0.25
                stop = 0.01
            elif exited < 1.0 and change >= 0.02:
                realized_pnl += partial_size * change
                exited += 0.25
                break  # все продано

            # === Стоп-лосс
            if stop is not None and change <= stop:
                remaining = 1.0 - exited
                realized_pnl += remaining * position_size * change
                exited = 1.0
                break

        if exited > 0:
            balance += realized_pnl
            trades.append({
                'time': entry_time,
                'position': position,
                'entry': entry_price,
                'exit': current_price,
                'pnl': round(realized_pnl, 2),
                'balance': round(balance, 2),
                'exited': f"{int(exited * 100)}%"
            })

    # === Збереження
    df_result = pd.DataFrame(trades)
    df_result.to_csv(f'{result_dir}/backtest_{symbol}_{tf}.csv', index=False)
    print(f"✅ {tf}: {len(df_result)} трейдів, фінальний баланс: ${balance:.2f}")
