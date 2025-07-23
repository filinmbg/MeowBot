import os
import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import f1_score
import tensorflow as tf
from tensorflow.keras import layers, models

# 🔧 Конфігурація
TIMEFRAMES = ['1d', '4h', '1h', '30m', '15m']
TARGETS = ['target_long', 'target_short']
DATA_DIR = 'test/data/BTCUSDT'
MODEL_DIR = 'Models/CNN'
BATCH_SIZE = 64
EPOCHS = 50
VALIDATION_SPLIT = 0.2

# 🔁 F1-колбек
class F1Callback(tf.keras.callbacks.Callback):
    def __init__(self, X_val, y_val):
        super().__init__()
        self.X_val = X_val
        self.y_val = y_val

    def on_epoch_end(self, epoch, logs=None):
        y_pred = (self.model.predict(self.X_val) > 0.5).astype(int)
        f1 = f1_score(self.y_val, y_pred)
        print(f"🔍 val_f1: {f1:.4f}")

# 📥 Завантаження CSV
def load_data(timeframe, target):
    suffix = target.split('_')[1]
    filename = f"BTCUSDT_{timeframe}_critical_indicators_with_targets_{suffix}.csv"
    filepath = os.path.join(DATA_DIR, filename)
    df = pd.read_csv(filepath)
    df.dropna(inplace=True)
    return df

# 🔃 Обробка X та y
def prepare_data(df, target, timeframe=None):
    # Колонки, які точно не є фічами
    drop_cols = ["open_time", "close_time", "open", "close", "target_long", "target_short"]
    drop_cols.remove(target)  # залишаємо тільки потрібний таргет

    # Формування X та y
    drop_cols += [target]
    X = df.drop(columns=[col for col in drop_cols if col in df.columns], errors="ignore")

    y = df[target]

    # 💾 Збереження списку фіч
    features_path = os.path.join(MODEL_DIR, f"features_{timeframe}_{target}.txt")
    with open(features_path, "w") as f:
        f.write("\n".join(X.columns))

    print(f"📊 Фічі для моделі ({target}): {X.shape[1]}")
    print(f"📋 Список: {list(X.columns)}")

    # Масштабування
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    X_scaled = X_scaled.reshape((X_scaled.shape[0], 1, X_scaled.shape[1]))

    return train_test_split(X_scaled, y, test_size=VALIDATION_SPLIT, shuffle=False)


# 🧠 CNN-модель
def build_model(input_shape):
    model = models.Sequential([
        tf.keras.Input(shape=input_shape),  # ✅ Явно задаємо форму
        layers.Conv1D(64, kernel_size=1, activation='relu'),
        layers.Dropout(0.3),
        layers.Conv1D(32, kernel_size=1, activation='relu'),
        layers.Flatten(),
        layers.Dense(64, activation='relu'),
        layers.Dense(1, activation='sigmoid')
    ])
    model.compile(
        optimizer='adam',
        loss='binary_crossentropy',
        metrics=[
            'accuracy',
            tf.keras.metrics.Precision(name='precision'),
            tf.keras.metrics.Recall(name='recall')
        ]
    )
    return model

# 🚀 Тренування + збереження
def train_and_save(timeframe, target, stats):
    print(f"\n📈 Тренування для: {timeframe.upper()} - {target}")
    df = load_data(timeframe, target)
    X_train, X_val, y_train, y_val = prepare_data(df, target)

    model = build_model(input_shape=(X_train.shape[1], X_train.shape[2]))

    f1_history = []

    class FinalF1(tf.keras.callbacks.Callback):
        def on_epoch_end(self, epoch, logs=None):
            y_pred = (self.model.predict(X_val) > 0.5).astype(int)
            f1 = f1_score(y_val, y_pred)
            f1_history.append(f1)
            print(f"🔍 val_f1: {f1:.4f}")

    history = model.fit(
        X_train, y_train,
        validation_data=(X_val, y_val),
        epochs=EPOCHS,
        batch_size=BATCH_SIZE,
        callbacks=[FinalF1()],
        verbose=2
    )

    # Зберігаємо модель
    os.makedirs(MODEL_DIR, exist_ok=True)
    model_path = os.path.join(MODEL_DIR, f"cnn_{timeframe}_{target}.keras")
    model.save(model_path)
    print(f"✅ Збережено модель: {model_path}")

    # Додаємо фінальні метрики до статистики
    stats.append({
        "timeframe": timeframe,
        "target": target,
        "val_accuracy": round(history.history["val_accuracy"][-1], 4),
        "precision": round(history.history["val_precision"][-1], 4),
        "recall": round(history.history["val_recall"][-1], 4),
        "f1": round(f1_history[-1], 4)
    })

def main():
    stats = []
    for tf in TIMEFRAMES:
        for target in TARGETS:
            try:
                train_and_save(tf, target, stats)
            except Exception as e:
                print(f"❌ Помилка {tf} {target}: {e}")

    # 📊 Фінальна таблиця
    df_stats = pd.DataFrame(stats)
    print("\n📈 Підсумкова статистика моделей:")
    print(df_stats.to_string(index=False))


if __name__ == "__main__":
    main()
