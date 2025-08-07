import os
import torch
import random
import argparse
import numpy as np
import pandas as pd
from tqdm import tqdm
from torch import nn, optim
from concurrent.futures import ProcessPoolExecutor
from configs import TIMEFRAMES, TARGETS, VARIANT_FEATURE_SETS

MODEL_DIR = "Models/DQN"
DATA_DIR = "test/data/BTCUSDT"
RESULTS_CSV = os.path.join(MODEL_DIR, "dqn_training_results.csv")
EPISODES = 50
GAMMA = 0.99
LR = 0.001
EPSILON = 0.1
BATCH_SIZE = 64
MAX_WORKERS = 2

os.makedirs(MODEL_DIR, exist_ok=True)


class DQN(nn.Module):
    def __init__(self, input_dim, output_dim):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, 64),
            nn.ReLU(),
            nn.Linear(64, 32),
            nn.ReLU(),
            nn.Linear(32, output_dim),
        )

    def forward(self, x):
        return self.net(x)


def train_model(timeframe, target, variant, features, force=False):
    name = f"DQN_{timeframe}_{target}_V{variant}"
    model_path = os.path.join(MODEL_DIR, f"{name}.pt")

    if os.path.exists(model_path) and not force:
        return name, "⏭️ already exists"

    try:
        csv_path = os.path.join(DATA_DIR, f"BTCUSDT_{timeframe}_critical_indicators_with_targets_{target}.csv")
        df = pd.read_csv(csv_path).dropna()

        if not all(f in df.columns for f in features):
            return name, f"💥 missing features: {[f for f in features if f not in df.columns]}"

        col_target = f"target_{target}" if f"target_{target}" in df.columns else "target"
        if col_target not in df.columns:
            return name, f"💥 '{col_target}' not found in dataset"

        X = df[features].values.astype(np.float32)
        y = df[col_target].values.astype(int)

        model = DQN(input_dim=X.shape[1], output_dim=2)
        optimizer = optim.Adam(model.parameters(), lr=LR)
        loss_fn = nn.MSELoss()

        for ep in range(EPISODES):
            states, targets = [], []
            for i in range(len(X) - 1):
                state = X[i]
                next_state = X[i + 1]
                action = y[i]

                reward = 1.0 if action == 1 else -1.0
                if random.random() < EPSILON:
                    next_action = random.randint(0, 1)
                else:
                    next_action = torch.argmax(model(torch.tensor(next_state))).item()

                target_q = reward + GAMMA * model(torch.tensor(next_state)).detach()[next_action]
                current_qs = model(torch.tensor(state)).detach().clone()
                current_qs[action] = target_q

                states.append(state)
                targets.append(current_qs.numpy())

                if len(states) >= BATCH_SIZE:
                    states_tensor = torch.tensor(np.array(states), dtype=torch.float32)
                    targets_tensor = torch.tensor(np.array(targets), dtype=torch.float32)
                    preds = model(states_tensor)
                    loss = loss_fn(preds, targets_tensor)
                    optimizer.zero_grad()
                    loss.backward()
                    optimizer.step()
                    states, targets = [], []

        torch.save(model.state_dict(), model_path)
        final_acc = np.mean([1 if model(torch.tensor(X[i])).argmax().item() == y[i] else 0 for i in range(len(X))])
        return name, "✅", round(final_acc * 100, 2)

    except Exception as e:
        return name, f"💥 {str(e)}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Force retrain even if model exists")
    args = parser.parse_args()
    force = args.force

    variants = list(VARIANT_FEATURE_SETS.keys())
    tasks = [(tf, tgt, v, VARIANT_FEATURE_SETS[v], force) for tf in TIMEFRAMES for tgt in TARGETS for v in variants]
    results = []

    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(train_model, *args): args for args in tasks}
        for i, future in enumerate(tqdm(futures, desc="🧠 Training DQN models")):
            name, status, *rest = future.result()
            msg = f"{name}.pt: {status}"
            if rest:
                msg += f", acc={rest[0]}%"
            print(f"🧠 {msg}")
            results.append({"model": name, "status": status, "accuracy_%": rest[0] if rest else None})

    pd.DataFrame(results).to_csv(RESULTS_CSV, index=False)


if __name__ == "__main__":
    main()
