import pandas as pd
import numpy as np
import os
import gym
from gym import spaces
from stable_baselines3 import PPO
from tqdm import tqdm

# 🔧 Параметри
timeframes = ["1d", "4h", "1h", "30m", "15m"]
targets = ["long", "short"]
data_dir = "test/data/BTCUSDT"
save_dir = "Models/PPO"
os.makedirs(save_dir, exist_ok=True)

results = []  # Для фінальної статистики


class PPOTradingEnv(gym.Env):
    def __init__(self, X, y, initial_balance=100.0, leverage=20):
        super().__init__()
        self.X = X.reset_index(drop=True).astype(np.float32)
        self.y = y.reset_index(drop=True).astype(int)
        self.initial_balance = initial_balance
        self.leverage = leverage
        self.action_space = spaces.Discrete(2)  # 0 = HOLD, 1 = OPEN
        self.observation_space = spaces.Box(low=-np.inf, high=np.inf, shape=(self.X.shape[1],), dtype=np.float32)

    def reset(self):
        self.current_step = 0
        self.balance = self.initial_balance
        return self._get_obs()

    def _get_obs(self):
        return self.X.iloc[self.current_step].values

    def step(self, action):
        done = False
        reward = 0
        price = self.X.iloc[self.current_step]["close"]
        signal = self.y.iloc[self.current_step]

        if action == 1:  # Відкрити трейд
            if signal == 1:
                reward = +self.leverage * self.balance * 0.01 * 0.01  # +1% прибутку
            else:
                reward = -self.leverage * self.balance * 0.01 * 0.01  # -1% збитку
            self.balance += reward

        self.current_step += 1
        if self.current_step >= len(self.X) - 1:
            done = True
        obs = self._get_obs() if not done else np.zeros_like(self.X.iloc[0].values)

        return obs, reward, done, {}

    def render(self, mode='human'):
        print(f"Step: {self.current_step}, Balance: {self.balance:.2f}")

    def get_final_balance(self):
        return self.balance


for tf in timeframes:
    for target in targets:
        print(f"\n📊 Тренування PPO для: {tf} / target_{target}")

        csv_path = os.path.join(data_dir, f"BTCUSDT_{tf}_critical_indicators_with_targets_{target}.csv")
        if not os.path.exists(csv_path):
            print(f"❌ Не знайдено: {csv_path}")
            continue

        df = pd.read_csv(csv_path).dropna()
        if f"target_{target}" not in df.columns:
            print(f"⚠️ Немає колонки target_{target} у {csv_path}")
            continue

        y = df[f"target_{target}"]
        X = df.drop(columns=[col for col in df.columns if col.startswith("target")])

        # Зберегти список фіч
        feature_list = list(X.columns)
        features_path = os.path.join(save_dir, f"features_{tf}_target_{target}.txt")
        with open(features_path, "w") as f:
            for feat in feature_list:
                f.write(feat + "\n")

        # PPO середовище
        env = PPOTradingEnv(X, y)
        model = PPO("MlpPolicy", env, verbose=0, tensorboard_log=f"./ppo_logs/{tf}_{target}")
        model.learn(total_timesteps=100_000)

        # Збереження моделі
        model_path = os.path.join(save_dir, f"ppo_{tf}_{target}")
        model.save(model_path)

        # Підсумкова метрика
        balance = env.get_final_balance()
        target_pct = round(100 * y.sum() / len(y), 2)

        results.append({
            "timeframe": tf,
            "target": target,
            "samples": len(y),
            "target_1_pct": target_pct,
            "final_balance": round(balance, 2),
            "model": model_path
        })

        print(f"✅ Модель збережена: {model_path}")
        print(f"ℹ️ Кількість зразків: {len(y)}, target=1: {target_pct}%, Баланс: ${round(balance, 2)}")

# 📊 Фінальна статистика
print("\n📈 Підсумкова статистика моделей:")
df_results = pd.DataFrame(results)
print(df_results.to_string(index=False))

# Зберегти CSV
df_results.to_csv(os.path.join(save_dir, "ppo_training_results.csv"), index=False)
