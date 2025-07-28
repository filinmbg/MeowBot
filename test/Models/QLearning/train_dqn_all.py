import os
import torch
import torch.nn as nn
import torch.optim as optim
import pandas as pd
import numpy as np
from sklearn.preprocessing import MinMaxScaler
from collections import deque
import random
from multiprocessing import Process, Semaphore
import gc

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

TIMEFRAMES = ['15m', '30m', '1h', '4h', '1d']
TARGETS = ['target_long', 'target_short']
DATA_DIR = 'test/data/BTCUSDT'
SAVE_DIR = 'Models/Qlearning'
LOG_DIR = 'logs/dqn'

os.makedirs(SAVE_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

class DQN(nn.Module):
    def __init__(self, input_dim, output_dim):
        super(DQN, self).__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, 256),
            nn.ReLU(),
            nn.Linear(256, 256),
            nn.ReLU(),
            nn.Linear(256, 128),
            nn.ReLU(),
            nn.Linear(128, output_dim)
        )
    def forward(self, x):
        return self.net(x)

class DQNAgent:
    def __init__(self, input_dim, action_dim, lr=1e-3, gamma=0.99, epsilon=1.0, decay=0.995):
        self.model = DQN(input_dim, action_dim).to(device)
        self.target = DQN(input_dim, action_dim).to(device)
        self.target.load_state_dict(self.model.state_dict())
        self.optimizer = optim.Adam(self.model.parameters(), lr=lr)
        self.loss_fn = nn.MSELoss()

        self.memory = deque(maxlen=10000)
        self.batch_size = 64
        self.gamma = gamma
        self.epsilon = epsilon
        self.decay = decay
        self.action_dim = action_dim

    def act(self, state):
        if np.random.rand() < self.epsilon:
            return random.randint(0, self.action_dim - 1)
        state = torch.tensor(state, dtype=torch.float32).unsqueeze(0).to(device)
        with torch.no_grad():
            q = self.model(state)
        return torch.argmax(q).item()

    def remember(self, state, action, reward, next_state, done):
        self.memory.append((state, action, reward, next_state, done))

    def train(self):
        if len(self.memory) < self.batch_size:
            return
        batch = random.sample(self.memory, self.batch_size)
        states, actions, rewards, next_states, dones = zip(*batch)

        states = torch.tensor(np.array(states), dtype=torch.float32).to(device)
        next_states = torch.tensor(np.array(next_states), dtype=torch.float32).to(device)
        actions = torch.tensor(actions).to(device)
        rewards = torch.tensor(rewards, dtype=torch.float32).to(device)
        dones = torch.tensor(dones).to(device)

        q_values = self.model(states)
        next_q = self.target(next_states)
        q_target = q_values.clone()

        for i in range(self.batch_size):
            target = rewards[i] if dones[i] else rewards[i] + self.gamma * torch.max(next_q[i])
            q_target[i, actions[i]] = target

        loss = self.loss_fn(q_values, q_target.detach())
        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()

    def update_target(self):
        self.target.load_state_dict(self.model.state_dict())

def train_dqn_for_pair(timeframe, target, sema):
    with sema:
        print(f"\n🚀 [{os.getpid()}] {timeframe}-{target} запускається на пристрої: {device}")
        if device.type == "cuda":
            print(f"    ➕ Використовується GPU: {torch.cuda.get_device_name(0)}")
        else:
            print(f"    ⚠️ Працює на CPU")

        try:
            print(f"📊 Навчання DQN: {timeframe} - {target}")

            filename = f'{DATA_DIR}/BTCUSDT_{timeframe}_critical_indicators_with_targets_{target.split("_")[1]}.csv'
            df = pd.read_csv(filename)

            if "trend_type" in df.columns:
                df["trend_type"] = df["trend_type"].map({
                    "uptrend": 1, "downtrend": -1, "flat": 0, "undefined": 0
                })

            cols_to_drop = [col for col in ["target_long", "target_short"] if col in df.columns]
            features = df.drop(columns=cols_to_drop).values
            scaler = MinMaxScaler()
            features = scaler.fit_transform(features)

            prices = df["close"].values
            labels = df[target].values

            state_dim = features.shape[1]
            print(f"📐 Вхідних фічей: {state_dim}")
            agent = DQNAgent(state_dim, action_dim=3)

            model_path = f'{SAVE_DIR}/dqn_{timeframe}_{target}.pt'
            log_path = f'{LOG_DIR}/dqn_stats_{timeframe}_{target}.csv'

            start_episode = 0
            log_data = []

            if os.path.exists(model_path):
                agent.model = torch.load(model_path, weights_only=False)
                agent.target.load_state_dict(agent.model.state_dict())
                print(f"📦 Модель завантажена: {model_path}")

            if os.path.exists(log_path):
                log_df = pd.read_csv(log_path)
                if not log_df.empty:
                    last_row = log_df.iloc[-1]
                    agent.epsilon = last_row["epsilon"]
                    start_episode = int(last_row["episode"]) + 1
                    log_data = log_df.values.tolist()
                    print(f"🔁 Продовження з епізоду {start_episode}, epsilon={agent.epsilon:.3f}")

            for episode in range(start_episode, 100):
                total_reward = 0
                for i in range(len(features) - 1):
                    state = features[i]
                    next_state = features[i + 1]
                    action = agent.act(state)

                    reward = 0
                    done = (i == len(features) - 2)

                    if action == 1:
                        reward = ((prices[i + 1] - prices[i]) / prices[i]) * 100
                    elif action == 2:
                        reward = ((prices[i] - prices[i + 1]) / prices[i]) * 100

                    agent.remember(state, action, reward, next_state, done)
                    agent.train()
                    total_reward += reward

                agent.update_target()
                agent.epsilon *= agent.decay

                log_data.append([episode, total_reward, agent.epsilon])
                print(f"  🎯 {timeframe}-{target} | Епізод {episode:3d} | Reward: {total_reward:.2f} | Epsilon: {agent.epsilon:.3f}")

                torch.save(agent.model, model_path)
                log_df = pd.DataFrame(log_data, columns=['episode', 'total_reward', 'epsilon'])
                log_df.to_csv(log_path, index=False)

            print(f"✅ Модель збережена: {model_path}")
            print(f"🧾 Метрики збережені: {log_path}")

        except Exception as e:
            print(f"❌ Помилка {timeframe}-{target}: {e}")

        finally:
            torch.cuda.empty_cache()
            gc.collect()

if __name__ == '__main__':
    from multiprocessing import set_start_method
    try:
        set_start_method("spawn")
    except RuntimeError:
        pass

    sema = Semaphore(10)
    processes = []

    for tf in TIMEFRAMES:
        for tg in TARGETS:
            p = Process(target=train_dqn_for_pair, args=(tf, tg, sema))
            p.start()
            processes.append(p)

    for p in processes:
        p.join()
