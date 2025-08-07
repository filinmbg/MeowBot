import os
import gym
import torch
import warnings
import numpy as np
import pandas as pd
from tqdm import tqdm
from sklearn.metrics import classification_report
from sklearn.preprocessing import StandardScaler
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv
from concurrent.futures import ProcessPoolExecutor
from configs import TIMEFRAMES, TARGETS, VARIANT_FEATURE_SETS
from gym import spaces

# Suppress warnings and logs
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'
warnings.filterwarnings("ignore", category=UserWarning)

# Paths
DATA_DIR = "test/data/BTCUSDT"
MODEL_DIR = "Models/PPO"
LOG_DIR = "Models/PPO/logs"
os.makedirs(MODEL_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

TOTAL_TIMESTEPS = 10_000
MAX_WORKERS = 10

class TradingEnv(gym.Env):
    metadata = {"render.modes": ["human"]}

    def __init__(self, df, features, window_size=30):
        super().__init__()
        self.df = df.reset_index(drop=True)
        self.features = features
        self.window_size = window_size
        self.current_step = 0
        self.total_steps = len(df) - 1

        self.observation_space = spaces.Box(
            low=-np.inf,
            high=np.inf,
            shape=(window_size, len(features)),
            dtype=np.float32
        )

        self.action_space = spaces.Discrete(3)  # 0 = hold, 1 = long, 2 = short
        self.position = 0
        self.entry_price = 0

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self.current_step = self.window_size
        self.position = 0
        self.entry_price = 0
        return self._get_observation(), {}

    def step(self, action):
        reward = 0
        done = False
        current_price = self.df.iloc[self.current_step]["close"]

        if self.position == 0:
            if action == 1:
                self.position = 1
                self.entry_price = current_price
            elif action == 2:
                self.position = 2
                self.entry_price = current_price
        else:
            if action == 0:
                pass
            elif (self.position == 1 and action == 2) or (self.position == 2 and action == 1):
                reward = self._get_profit(current_price)
                self.position = action
                self.entry_price = current_price

        self.current_step += 1
        if self.current_step >= self.total_steps:
            done = True
            reward += self._get_profit(current_price)

        return self._get_observation(), reward, done, {}

    def _get_profit(self, current_price):
        if self.position == 1:
            return current_price - self.entry_price
        elif self.position == 2:
            return self.entry_price - current_price
        return 0

    def _get_observation(self):
        window = self.df.iloc[self.current_step - self.window_size:self.current_step][self.features]
        return window.values.astype(np.float32)

def train_single_model(args):
    timeframe, target, variant, features = args
    model_name = f"PPO_{timeframe}_{target}_V{variant}"
    model_path = os.path.join(MODEL_DIR, model_name, "model.zip")
    log_path = os.path.join(MODEL_DIR, model_name, "log.txt")
    os.makedirs(os.path.dirname(model_path), exist_ok=True)

    print(f"[{model_name}] 🚀 Training started")

    if os.path.exists(model_path):
        return f"[{model_name}] ✅ Already trained"

    try:
        file_path = os.path.join(DATA_DIR, f"BTCUSDT_{timeframe}_critical_indicators_with_targets_{target}.csv")
        if not os.path.exists(file_path):
            return f"[{model_name}] ❌ Missing file: {file_path}"

        df = pd.read_csv(file_path).dropna()
        if "close" not in df.columns:
            return f"[{model_name}] ❌ Missing 'close' column"

        target_col = f"target_{target}" if f"target_{target}" in df.columns else "target"
        if target_col not in df.columns:
            return f"[{model_name}] ❌ Missing target column"

        missing = [f for f in features if f not in df.columns]
        if missing:
            return f"[{model_name}] ❌ Missing features: {missing}"

        y = df[target_col].values.astype(int)
        if np.all(y == 0):
            return f"[{model_name}] ⚠️ Only HOLD samples"

        scaler = StandardScaler()
        df[features] = scaler.fit_transform(df[features])

        def make_env():
            return TradingEnv(df, features)

        env = DummyVecEnv([make_env])
        model = PPO("MlpPolicy", env, verbose=0, device="cuda" if torch.cuda.is_available() else "cpu")
        model.learn(total_timesteps=TOTAL_TIMESTEPS)
        model.save(model_path)

        obs, _ = env.reset()
        predictions, truths = [], []

        for i in range(len(df) - 30):
            action, _ = model.predict(obs, deterministic=True)

            # Ensure action is scalar
            if isinstance(action, np.ndarray):
                action_val = int(action[0])
            else:
                action_val = int(action)

            predictions.append(action_val)
            truths.append(y[i + 30])

            try:
                obs, reward, done, info = env.step([action_val])  # wrap in list for DummyVecEnv
                if isinstance(done, np.ndarray):
                    if done[0]:
                        break
                elif done:
                    break
            except Exception as e:
                print(f"[{model_name}] ❌ step() error at i={i}: {str(e)}")
                return f"[{model_name}] ❌ error: {str(e)}"

        report = classification_report(truths, predictions, zero_division=0, output_dict=True)
        acc = report["accuracy"]
        f1 = report["1"]["f1-score"]
        prec = report["1"]["precision"]
        recall = report["1"]["recall"]
        winrate = np.mean(np.array(predictions) == np.array(truths))

        summary = (
            f"[{model_name}] ✅ Trained\n"
            f"  - Accuracy: {acc:.4f}\n"
            f"  - Precision: {prec:.4f}\n"
            f"  - Recall: {recall:.4f}\n"
            f"  - F1-score: {f1:.4f}\n"
            f"  - Winrate: {winrate:.4f}"
        )
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(summary + "\n\n")
            f.write(classification_report(truths, predictions, zero_division=0))

        return summary

    except Exception as e:
        print(f"[{model_name}] ❌ Top-level error: {str(e)}")
        return f"[{model_name}] ❌ error: {str(e)}"

def main():
    print(f"🧠 Starting PPO training on {len(TIMEFRAMES) * len(TARGETS) * len(VARIANT_FEATURE_SETS)} models with {MAX_WORKERS} workers...\n")
    tasks = [
        (timeframe, target, variant, features)
        for timeframe in TIMEFRAMES
        for target in TARGETS
        for variant, features in VARIANT_FEATURE_SETS.items()
    ]

    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
        results = list(tqdm(executor.map(train_single_model, tasks), total=len(tasks)))

    for r in results:
        print(r)

if __name__ == "__main__":
    main()
