import os
import numpy as np  # ✅ додано для перевірок на NaN / Inf
import pandas as pd
import torch
import torch.nn as nn
import torch.optim as optim
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed

from configs import TIMEFRAMES, TARGETS, VARIANT_FEATURE_SETS


class SimpleTransformer(nn.Module):
    def __init__(self, input_size, d_model=64, nhead=4, num_layers=2, output_size=1):
        super(SimpleTransformer, self).__init__()
        self.embedding = nn.Linear(input_size, d_model)
        encoder_layer = nn.TransformerEncoderLayer(d_model=d_model, nhead=nhead, batch_first=True)
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
        self.fc = nn.Linear(d_model, output_size)
        self.activation = nn.Sigmoid()  # ✅ гарантує вихід у [0, 1]

    def forward(self, x):
        x = self.embedding(x)
        x = self.transformer(x)
        x = x.mean(dim=1)
        x = self.fc(x)
        return self.activation(x)  # ✅ гарантовано застосовує sigmoid


def train_transformer(timeframe, target, variant_id, features):
    model_name = f"Transformer_{timeframe}_{target}_V{variant_id}"
    csv_path = f"test/data/BTCUSDT/BTCUSDT_{timeframe}_critical_indicators_with_targets_{target}.csv"
    save_path = f"Models/Transformer/{model_name}.pt"

    try:
        if os.path.exists(save_path):
            return model_name, None, "⏭️ already exists"

        if not os.path.exists(csv_path):
            return model_name, None, "❌ file not found"

        df = pd.read_csv(csv_path)

        target_column = f"target_{target}" if f"target_{target}" in df.columns else "target"
        if target_column not in df.columns:
            return model_name, None, f"❌ '{target_column}' column missing"

        import numpy as np
        df = df.replace([np.inf, -np.inf], np.nan).dropna(subset=features + [target_column])

        X = df[features].values
        y = df[target_column].values

        scaler = StandardScaler()
        X = scaler.fit_transform(X)
        X = X.reshape((X.shape[0], 1, X.shape[1]))

        X_train, X_val, y_train, y_val = train_test_split(X, y, test_size=0.2, random_state=42)

        X_train_tensor = torch.tensor(X_train, dtype=torch.float32)
        y_train_tensor = torch.tensor(y_train, dtype=torch.float32).unsqueeze(1)
        X_val_tensor = torch.tensor(X_val, dtype=torch.float32)
        y_val_tensor = torch.tensor(y_val, dtype=torch.float32).unsqueeze(1)

        model = SimpleTransformer(input_size=X.shape[2])
        criterion = nn.BCELoss()
        optimizer = optim.Adam(model.parameters(), lr=0.001)

        best_loss = float('inf')
        patience = 10
        counter = 0

        for epoch in range(100):
            model.train()
            optimizer.zero_grad()
            outputs = model(X_train_tensor)
            if torch.isnan(outputs).any() or torch.isinf(outputs).any():
                raise RuntimeError("Train output has NaN or Inf")
            loss = criterion(outputs, y_train_tensor)
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
                    break

        model.load_state_dict(torch.load(save_path))
        model.eval()
        with torch.no_grad():
            preds = model(X_val_tensor)
            predicted = (preds > 0.5).float()
            accuracy = (predicted == y_val_tensor).float().mean().item()

        return model_name, accuracy, "✅ trained"

    except Exception as e:
        return model_name, None, f"❌ error: 💥 {type(e).__name__}: {str(e)}"

def main():
    results = []
    tasks = []

    with ThreadPoolExecutor(max_workers=2) as executor:
        for timeframe in TIMEFRAMES:
            for target in TARGETS:
                for variant_id, features in VARIANT_FEATURE_SETS.items():
                    tasks.append(executor.submit(train_transformer, timeframe, target, variant_id, features))

        for future in tqdm(as_completed(tasks), total=len(tasks), desc="🧠 Training Transformer models"):
            try:
                model_name, accuracy, status = future.result()
            except Exception as e:
                model_name, accuracy, status = "❌ error", None, f"💥 {type(e).__name__}: {e}"

            results.append({
                "model": model_name,
                "accuracy": accuracy,
                "status": status
            })
            print(f"{model_name}: {status}")

    # Save training results
    results_df = pd.DataFrame(results)
    os.makedirs("Models/Transformer", exist_ok=True)
    results_df.to_csv("Models/Transformer/transformer_training_results.csv", index=False)
    print("\n📊 Training results saved to Models/Transformer/transformer_training_results.csv")



if __name__ == "__main__":
    main()
