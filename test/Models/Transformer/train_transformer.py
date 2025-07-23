import argparse
import os
import pandas as pd
import numpy as np
import torch
import torch.nn as nn
from tqdm import tqdm
from torch.utils.data import Dataset, DataLoader
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split

SEED = 42
torch.manual_seed(SEED)
np.random.seed(SEED)

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
WINDOW_SIZE = 32
BATCH_SIZE = 32
EPOCHS = 10

class SequenceDataset(Dataset):
    def __init__(self, df, features, target, window_size):
        self.X, self.y = [], []
        for i in range(len(df) - window_size):
            seq_x = df[features].iloc[i:i+window_size].values
            seq_y = df[target].iloc[i+window_size]
            if not np.isnan(seq_x).any() and not pd.isna(seq_y):
                self.X.append(seq_x)
                self.y.append(seq_y)

        if len(self.X) == 0:
            raise ValueError("❌ Увага: Dataset порожній після фільтрації. Можливо, всі записи мають NaN.")

        self.X = np.array(self.X, dtype=np.float32)
        self.X = (self.X - self.X.mean()) / (self.X.std() + 1e-8)
        self.X = np.nan_to_num(self.X, nan=0.0, posinf=0.0, neginf=0.0)
        self.X = torch.tensor(self.X, dtype=torch.float32)
        self.y = torch.tensor(self.y, dtype=torch.long)

    def __len__(self):
        return len(self.X)

    def __getitem__(self, idx):
        return self.X[idx], self.y[idx]

class TransformerModel(nn.Module):
    def __init__(self, input_dim, seq_len, num_classes=2):
        super().__init__()
        self.encoder = nn.Linear(input_dim, input_dim)
        encoder_layer = nn.TransformerEncoderLayer(d_model=input_dim, nhead=1, dim_feedforward=128)
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=2)
        self.fc = nn.Linear(input_dim * seq_len, num_classes)

    def forward(self, x):
        x = self.encoder(x)
        x = self.transformer(x)
        x = x.view(x.size(0), -1)
        return self.fc(x)

def train_transformer_model(timeframe, target):
    df_path = f"test/data/BTCUSDT/BTCUSDT_{timeframe}_indicators_with_targets_{target}.csv"
    df = pd.read_csv(df_path)
    print(f"✅ Розмір df: {df.shape}")

    target_col = f"target_{target}"
    class_counts = df[target_col].value_counts()
    print("🔢 Кількість класів:\n", class_counts)

    features = df.columns.drop([target_col, "open_time"]).tolist()
    print(f"\n🔍 Використано {len(features)} фіч: {features}")

    dataset = SequenceDataset(df, features, target_col, WINDOW_SIZE)
    print("📊 Перевірка на NaN в X:", torch.isnan(dataset.X).any().item())
    print("📊 Перевірка на Inf в X:", torch.isinf(dataset.X).any().item())
    print(f"📦 Розмір датасету: {len(dataset)}")

    indices = np.arange(len(dataset))
    labels = dataset.y.numpy()
    train_idx, test_idx = train_test_split(indices, test_size=0.2, stratify=labels, random_state=SEED)

    train_data = torch.utils.data.Subset(dataset, train_idx)
    test_data = torch.utils.data.Subset(dataset, test_idx)

    train_loader = DataLoader(train_data, batch_size=BATCH_SIZE, shuffle=True)
    test_loader = DataLoader(test_data, batch_size=BATCH_SIZE)

    model = TransformerModel(input_dim=len(features), seq_len=WINDOW_SIZE).to(DEVICE)

    class_weights = 1. / torch.tensor([class_counts[0], class_counts[1]], dtype=torch.float32)
    class_weights = class_weights / class_weights.sum()
    criterion = nn.CrossEntropyLoss(weight=class_weights.to(DEVICE))
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-4)

    for epoch in range(EPOCHS):
        model.train()
        total_loss = 0
        for X_batch, y_batch in train_loader:
            X_batch, y_batch = X_batch.to(DEVICE), y_batch.to(DEVICE)
            optimizer.zero_grad()
            output = model(X_batch)
            loss = criterion(output, y_batch)
            loss.backward()
            optimizer.step()
            total_loss += loss.item()
        print(f"Epoch {epoch+1}/{EPOCHS}, Loss: {total_loss / len(train_loader):.4f}")

    for epoch in range(EPOCHS):
        model.train()
        total_loss = 0

        # tqdm на кожну епоху
        loop = tqdm(train_loader, desc=f"Epoch {epoch + 1}/{EPOCHS}", leave=False)
        for X_batch, y_batch in loop:
            X_batch, y_batch = X_batch.to(DEVICE), y_batch.to(DEVICE)

            optimizer.zero_grad()
            output = model(X_batch)
            loss = criterion(output, y_batch)
            loss.backward()
            optimizer.step()

            total_loss += loss.item()
            loop.set_postfix(loss=loss.item())

        avg_loss = total_loss / len(train_loader)
        print(f"📉 Epoch {epoch + 1}/{EPOCHS}, Loss: {avg_loss:.4f}")

    model.eval()
    all_preds, all_targets = [], []
    with torch.no_grad():
        for X_batch, y_batch in test_loader:
            X_batch = X_batch.to(DEVICE)
            preds = torch.argmax(model(X_batch), dim=1).cpu().numpy()
            all_preds.extend(preds)
            all_targets.extend(y_batch.numpy())

    print(f"\n🧠 [Transformer {timeframe.upper()} / {target.upper()}] Звіт точності:")
    print(classification_report(all_targets, all_preds, zero_division=0))

    os.makedirs("Models/Transformer", exist_ok=True)
    torch.save(model.state_dict(), f"Models/Transformer/transformer_{timeframe}_{target}.pth")
    with open(f"Models/Transformer/transformer_{timeframe}_{target}_features.txt", "w") as f:
        f.write("\n".join(features))
    print(f"✅ Модель збережено → Models/Transformer/transformer_{timeframe}_{target}.pth")
    print(f"📄 Фічі збережено → Models/Transformer/transformer_{timeframe}_{target}_features.txt")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeframe", required=True)
    parser.add_argument("--target", choices=["long", "short"], required=True)
    args = parser.parse_args()
    train_transformer_model(args.timeframe, args.target)

if __name__ == "__main__":
    main()
