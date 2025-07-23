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
            nn.Linear(input_dim, 64),
            nn.ReLU(),
            nn.Linear(64, 64),
            nn.ReLU(),
            nn.Linear(64, output_dim)
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
        import os

        # 🔍 Вивід інформації про пристрій
        print(f"\n🚀 [{os.getpid()}] {timeframe}-{target} запускається на пристрої: {device}")
        if device.type == "cuda":
            print(f"    ➕ Використовується GPU: {torch.cuda.get_device_name(0)}")
        else:
            print(f"    ⚠️ Працює на CPU")

        try:
            print(f"📊 Навчання DQN: {timeframe} - {target}")

            filename = f'{DATA_DIR}/BTCUSDT_{timeframe}_critical_indicators_with_targets_{target.split("_")[1]}.csv'
            df = pd.read_csv(filename)

            features = df.drop(columns=[target]).values
            scaler = MinMaxScaler()
            features = scaler.fit_transform(features)

            prices = df['close'].values
            labels = df[target].values

            state_dim = features.shape[1]
            agent = DQNAgent(state_dim, action_dim=3)

            log_data = []

            for episode in range(100):
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

            model_path = f'{SAVE_DIR}/dqn_{timeframe}_{target}.pt'
            torch.save(agent.model, model_path)
            print(f"✅ Модель збережена: {model_path}")

            log_df = pd.DataFrame(log_data, columns=['episode', 'total_reward', 'epsilon'])
            log_path = f'{LOG_DIR}/dqn_stats_{timeframe}_{target}.csv'
            log_df.to_csv(log_path, index=False)
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

    sema = Semaphore(5)  # максимум 3 одночасно
    processes = []

    for tf in TIMEFRAMES:
        for tg in TARGETS:
            p = Process(target=train_dqn_for_pair, args=(tf, tg, sema))
            p.start()
            processes.append(p)

    for p in processes:
        p.join()
