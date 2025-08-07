import os
import pandas as pd
import numpy as np
import pickle
from sklearn.preprocessing import KBinsDiscretizer
from concurrent.futures import ProcessPoolExecutor, as_completed
from configs import TIMEFRAMES, TARGETS, VARIANT_FEATURE_SETS
import warnings
warnings.filterwarnings("ignore", category=UserWarning)

DATA_DIR = "test/data/BTCUSDT"
SAVE_DIR = "Models/Qlearning/saved_models"
RESULT_CSV = "Models/Qlearning/qlearning_training_results.csv"
EPISODES = 1000
ALPHA = 0.1
GAMMA = 0.95
EPSILON_START = 1.0
EPSILON_MIN = 0.01
EPSILON_DECAY = 0.995
N_BINS = 3
RANDOM_SEED = 42
np.random.seed(RANDOM_SEED)

def train_qlearning(timeframe, target, variant):
    try:
        model_name = f"Q_{timeframe}_{target}_{variant}"
        csv_path = os.path.join(DATA_DIR, f"BTCUSDT_{timeframe}_critical_indicators_with_targets_{target}.csv")
        if not os.path.exists(csv_path):
            return {"model": model_name, "status": "❌ no CSV"}

        df = pd.read_csv(csv_path).dropna()

        features = VARIANT_FEATURE_SETS[variant]
        if len(features) > 8:
            features = features[:8]  # ❗ жорстке обмеження

        if not all(f in df.columns for f in features):
            return {"model": model_name, "status": f"💥 missing features in CSV"}

        X = df[features].copy()
        target_col = f"target_{target}"
        if target_col not in df.columns:
            return {"model": model_name, "status": f"💥 column '{target_col}' not in CSV"}
        y = df[target_col].values

        # Дискретизація
        scaler = KBinsDiscretizer(n_bins=3, encode="ordinal", strategy="uniform")  # ↓ 3 біна
        X_disc = scaler.fit_transform(X).astype(int)
        state_shape = tuple(X_disc.max(axis=0) + 1)
        Q_shape = state_shape + (2,)
        q_size = np.prod(Q_shape)

        print(f"📦 {model_name}: Q-table shape = {Q_shape}, total = {q_size:,} cells")

        if q_size > 1e7:
            return {"model": model_name, "status": f"💥 Q-table too large: {q_size:,} cells"}

        Q = np.zeros(Q_shape)
        epsilon = EPSILON_START
        total_reward = 0

        for ep in range(EPISODES):
            ep_reward = 0
            for state, action_true in zip(X_disc, y):
                state = tuple(state)
                if np.random.rand() < epsilon:
                    action = np.random.choice([0, 1])
                else:
                    action = np.argmax(Q[state])

                reward = 1 if action == action_true and action == 1 else 0
                best_next = np.max(Q[state])
                Q[state][action] += ALPHA * (reward + GAMMA * best_next - Q[state][action])
                ep_reward += reward

            epsilon = max(EPSILON_MIN, epsilon * EPSILON_DECAY)
            total_reward = ep_reward

        # Збереження моделі
        save_path = os.path.join(SAVE_DIR, timeframe, target)
        os.makedirs(save_path, exist_ok=True)
        with open(os.path.join(save_path, f"Q_{variant}.pkl"), "wb") as f:
            pickle.dump((Q, scaler), f)

        return {
            "model": model_name,
            "timeframe": timeframe,
            "target": target,
            "variant": variant,
            "episodes": EPISODES,
            "final_reward": total_reward,
            "epsilon": round(epsilon, 4),
            "status": "✅"
        }

    except Exception as e:
        return {"model": model_name, "status": f"💥 {str(e)}"}


def main():
    tasks = []
    for timeframe in TIMEFRAMES:
        for target in TARGETS:
            for variant in VARIANT_FEATURE_SETS.keys():
                tasks.append((timeframe, target, variant))

    results = []
    with ProcessPoolExecutor() as executor:
        futures = [executor.submit(train_qlearning, tf, tg, var) for tf, tg, var in tasks]
        for f in as_completed(futures):
            res = f.result()
            print(f"🧠 {res['model']}: {res.get('status', '')}")
            results.append(res)

    df = pd.DataFrame(results)
    df.to_csv(RESULT_CSV, index=False)
    print(f"\n📊 Результати збережено у {RESULT_CSV}")

if __name__ == "__main__":
    main()
