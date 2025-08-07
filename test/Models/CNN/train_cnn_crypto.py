import os
import torch
import torch.nn as nn
import torch.optim as optim
import pandas as pd
import numpy as np
from tqdm import tqdm
import warnings
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, f1_score
from configs import TIMEFRAMES, TARGETS, VARIANT_FEATURE_SETS

warnings.filterwarnings("ignore", category=FutureWarning)

class SimpleCNN(nn.Module):
    def __init__(self, input_size):
        super(SimpleCNN, self).__init__()
        self.conv1 = nn.Conv1d(in_channels=1, out_channels=16, kernel_size=3, padding=1)
        self.pool = nn.AdaptiveAvgPool1d(1)
        self.fc1 = nn.Linear(16, 1)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        # x: (batch, 1, num_features)
        x = torch.relu(self.conv1(x))  # (batch, 16, num_features)
        x = self.pool(x)               # (batch, 16, 1)
        x = x.view(x.size(0), -1)      # (batch, 16)
        x = self.fc1(x)                # (batch, 1)
        return self.sigmoid(x)


def train_model(timeframe, target, variant_id, features):
    model_name = f"CNN_{timeframe}_{target}_V{variant_id}"
    save_path = f"Models/CNN/{model_name}.pt"
    csv_path = f"test/data/BTCUSDT/BTCUSDT_{timeframe}_critical_indicators_with_targets_{target}.csv"

    if os.path.exists(save_path):
        return model_name, None, None, "⏭️ already exists"

    if not os.path.exists(csv_path):
        return model_name, None, None, "❌ file missing"

    try:
        df = pd.read_csv(csv_path)

        # перевірка наявності всіх фіч
        missing = [f for f in features if f not in df.columns]
        if missing:
            return model_name, None, None, f"❌ missing features: {missing}"

        target_column = f"target_{target}" if f"target_{target}" in df.columns else "target"
        if target_column not in df.columns:
            return model_name, None, None, f"❌ missing target column: {target_column}"

        X = df[features].values
        y = df[target_column].values

        # NaN check
        if np.isnan(X).any() or np.isnan(y).any():
            return model_name, None, None, "❌ NaN in data"

        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        X_scaled = X_scaled.reshape((X_scaled.shape[0], 1, X_scaled.shape[1]))  # (samples, 1, features)

        X_train, X_val, y_train, y_val = train_test_split(X_scaled, y, test_size=0.2, random_state=42)

        X_train_tensor = torch.tensor(X_train, dtype=torch.float32)
        y_train_tensor = torch.tensor(y_train, dtype=torch.float32).unsqueeze(1)
        X_val_tensor = torch.tensor(X_val, dtype=torch.float32)
        y_val_tensor = torch.tensor(y_val, dtype=torch.float32).unsqueeze(1)

        model = SimpleCNN(input_size=X_train_tensor.shape[2])
        criterion = nn.BCELoss()
        optimizer = optim.Adam(model.parameters(), lr=0.001)

        best_loss = float('inf')
        patience, counter = 10, 0

        for epoch in range(100):
            model.train()
            optimizer.zero_grad()
            outputs = model(X_train_tensor)
            loss = criterion(outputs, y_train_tensor)
            if torch.isnan(loss) or torch.isinf(loss):
                return model_name, None, None, "❌ loss is NaN or Inf"
            loss.backward()
            optimizer.step()

            model.eval()
            with torch.no_grad():
                val_outputs = model(X_val_tensor)
                val_loss = criterion(val_outputs, y_val_tensor).item()

            if val_loss < best_loss:
                best_loss = val_loss
                torch.save(model.state_dict(), save_path)
                counter = 0
            else:
                counter += 1
                if counter >= patience:
                    break  # early stopping

        model.load_state_dict(torch.load(save_path))
        model.eval()
        with torch.no_grad():
            preds = model(X_val_tensor)
            predicted = (preds > 0.5).float()
            acc = accuracy_score(y_val_tensor.numpy(), predicted.numpy())
            f1 = f1_score(y_val_tensor.numpy(), predicted.numpy(), zero_division=0)

        return model_name, acc, f1, "✅ trained"

    except Exception as e:
        return model_name, None, None, f"❌ error: {type(e).__name__}: {str(e)}"



def main():
    os.makedirs("Models/CNN", exist_ok=True)
    results = []
    total = len(TIMEFRAMES) * len(TARGETS) * len(VARIANT_FEATURE_SETS)

    with tqdm(total=total, desc="🧠 Training CNN models") as pbar:
        for timeframe in TIMEFRAMES:
            for target in TARGETS:
                for variant_id, features in VARIANT_FEATURE_SETS.items():
                    model_name, acc, f1, status = train_model(timeframe, target, variant_id, features)
                    results.append({
                        "model": model_name,
                        "accuracy": acc,
                        "f1_score": f1,
                        "status": status
                    })
                    print(f"{model_name}: {status}")
                    pbar.update(1)

    df = pd.DataFrame(results)
    df.to_csv("Models/CNN/cnn_training_results.csv", index=False)
    print("📊 Results saved to Models/CNN/cnn_training_results.csv")


if __name__ == "__main__":
    main()
