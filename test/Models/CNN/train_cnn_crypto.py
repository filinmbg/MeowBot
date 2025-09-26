# test/Models/CNN/train_cnn_crypto.py
import os
import sys
import json
import math
import argparse
import logging
from pathlib import Path
from datetime import datetime
from typing import List
import warnings

warnings.filterwarnings("ignore", category=UserWarning)

import numpy as np
import pandas as pd
from tqdm import tqdm

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader, Subset

try:
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import (
        f1_score,
        accuracy_score,
        balanced_accuracy_score,
        classification_report,
        confusion_matrix,
    )
except Exception as e:
    raise RuntimeError("Потрібен scikit-learn: pip install scikit-learn") from e

# --- load sibling configs.py (always the one next to this file) ---------------
import importlib.util, pathlib
_THIS = pathlib.Path(__file__).resolve()
_CFG  = _THIS.parent / "configs.py"
spec = importlib.util.spec_from_file_location("model_configs", _CFG)
_cfg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_cfg)
TIMEFRAMES = _cfg.TIMEFRAMES
TARGETS = _cfg.TARGETS
VARIANT_FEATURE_SETS = _cfg.VARIANT_FEATURE_SETS


# ===================== Модель =====================
class SimpleCNN1D(nn.Module):
    """
    Легка 1D-CNN для бінарної класифікації.
    Вхід: (batch, channels, lookback)
    """
    def __init__(self, in_ch: int, lookback: int):
        super().__init__()
        self.fe = nn.Sequential(
            nn.Conv1d(in_ch, 32, kernel_size=5, padding=2),
            nn.ReLU(inplace=True),
            nn.Conv1d(32, 64, kernel_size=5, padding=2),
            nn.ReLU(inplace=True),
            nn.AdaptiveAvgPool1d(1),
            nn.Flatten()
        )
        self.classifier = nn.Sequential(
            nn.LazyLinear(128),
            nn.ReLU(inplace=True),
            nn.Linear(128, 2)
        )

    def forward(self, x):
        x = self.fe(x)
        x = self.classifier(x)
        return x


# ===================== Дейтасет =====================
class WindowedCSVDataset(Dataset):
    def __init__(self, df: pd.DataFrame, features: List[str], target_col: str, lookback: int):
        self.features = features
        self.target_col = target_col
        self.lookback = int(lookback)

        # Масиви
        X = df[features].values.astype(np.float32)
        y = df[target_col].values.astype(np.int64)

        # Нормалізація
        self.scaler = StandardScaler()
        X = self.scaler.fit_transform(X).astype(np.float32)

        # Побудова вікон (N, lookback, F)
        windows = []
        targets = []
        for i in range(lookback - 1, len(X)):
            windows.append(X[i - lookback + 1:i + 1])
            targets.append(y[i])
        W = np.asarray(windows, dtype=np.float32)   # (N, T, F)
        self.X = np.transpose(W, (0, 2, 1))         # (N, F, T) -> для Conv1d
        self.targets = np.asarray(targets, dtype=np.int64)

        # Індекси для сплітів (хронологічний)
        self.indices = np.arange(len(self.X), dtype=int)

    def __len__(self):
        return len(self.X)

    def __getitem__(self, i: int):
        # повертати (channels, lookback)
        window = self.X[i]
        y = self.targets[i]
        return torch.from_numpy(window), torch.tensor(y, dtype=torch.long)


# ===================== Утиліти =====================
def setup_logger(logs_dir: Path) -> logging.Logger:
    logs_dir.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("cnn_train")
    log.setLevel(logging.INFO)
    if log.handlers:
        for h in list(log.handlers):
            log.removeHandler(h)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    fh = logging.FileHandler(logs_dir / f"train_{ts}.log", encoding="utf-8")
    ch = logging.StreamHandler(sys.stdout)
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    fh.setFormatter(fmt); ch.setFormatter(fmt)
    log.addHandler(fh); log.addHandler(ch)
    return log


def find_training_csv(data_root: Path, symbol: str, tf: str, target: str) -> Path:
    """
    Шукає файл з індикаторами/таргетами для тренування в теці data_root (вже тека символу).
    Очікувані імена:
      SYMBOL_TF_critical_indicators_with_targets_TARGET.csv
      SYMBOL_TF_indicators_with_targets_TARGET.csv
      SYMBOL_TF_critical_indicators.csv
      SYMBOL_TF_indicators.csv
    """
    candidates = [
        data_root / f"{symbol}_{tf}_critical_indicators_with_targets_{target}.csv",
        data_root / f"{symbol}_{tf}_indicators_with_targets_{target}.csv",
        data_root / f"{symbol}_{tf}_critical_indicators.csv",
        data_root / f"{symbol}_{tf}_indicators.csv",
    ]
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError(
        f"Не знайдено CSV для {symbol} {tf} {target}. Шукав:\n" + "\n".join(map(str, candidates))
    )


def normalize_variant_sets(variant_sets_raw, fallback_input_col: str, df_sample: pd.DataFrame | None = None) -> List[List[str]]:
    """
    Приводить VARIANT_FEATURE_SETS до списку списків фіч.
    - dict {1:[...], 2:[...]} → відсортований список [[...], [...], ...]
    - list/tuple → [[...], [...], ...]
    - None/порожньо → [[fallback_input_col]] або альтернатива з df_sample
    """
    if isinstance(variant_sets_raw, dict):
        keys = sorted(variant_sets_raw.keys(), key=lambda x: int(x) if str(x).isdigit() else str(x))
        arr = [variant_sets_raw[k] for k in keys]
    elif isinstance(variant_sets_raw, (list, tuple)):
        arr = list(variant_sets_raw)
    else:
        arr = []

    if len(arr) > 0:
        def _as_feature_list(v):
            if isinstance(v, (list, tuple, set)):
                return list(v)
            return [v]
        return [_as_feature_list(v) for v in arr]

    # fallback: один канал (напр. 'close')
    if df_sample is not None and fallback_input_col not in df_sample.columns:
        for alt in ["close", "Close", "CLOSE", "last", "price"]:
            if alt in df_sample.columns:
                return [[alt]]
    return [[fallback_input_col]]


def pick_features(df: pd.DataFrame, variant_features, target_col: str, fallback_input_col: str) -> List[str]:
    """
    - беремо перетин variant_features з df.columns (без target)
    - якщо перетин порожній → fallback_input_col або типову OHLC
    """
    feats = [c for c in variant_features if c in df.columns and c != target_col]
    if len(feats) > 0:
        return feats
    if fallback_input_col in df.columns:
        return [fallback_input_col]
    for alt in ["close", "Close", "CLOSE", "last", "price"]:
        if alt in df.columns:
            return [alt]
    raise KeyError(f"Колонка '{fallback_input_col}' не знайдена і не вдалося підібрати альтернативу.")


def seed_everything(seed=42):
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def chronological_split(n_total: int, val_frac: float = 0.15):
    """
    Повертає (train_idx, val_idx) для хронологічного спліту.
    """
    n_val = max(1, int(round(n_total * val_frac)))
    idx = np.arange(n_total, dtype=int)
    return idx[:-n_val], idx[-n_val:]


# ===================== Тренування =====================
def make_class_weights(dataset: WindowedCSVDataset, train_idx: List[int], device: torch.device) -> torch.Tensor:
    """
    Обчислюємо ваги класів на train-підмножині (для CrossEntropyLoss).
    """
    y_indexed = dataset.targets[dataset.indices]   # y для кожного sample
    y_train = y_indexed[np.array(train_idx, dtype=int)]
    n_pos = np.sum(y_train == 1)
    n_neg = np.sum(y_train == 0)
    n_pos = max(1, int(n_pos)); n_neg = max(1, int(n_neg))
    w_pos = (n_neg + n_pos) / (2.0 * n_pos)
    w_neg = (n_neg + n_pos) / (2.0 * n_neg)
    return torch.tensor([w_neg, w_pos], dtype=torch.float32, device=device)


def train_one_model(
    df: pd.DataFrame,
    features: List[str],
    target_col: str,
    lookback: int,
    device: torch.device,
    logger: logging.Logger,
    epochs: int = 50,
    batch_size: int = 256,
    lr: float = 1e-3,
    num_workers: int = 0,
    patience: int = 12,
    min_delta: float = 1e-3,  # не використовується в ES, залишено для сумісності CLI
):
    dataset = WindowedCSVDataset(df, features, target_col, lookback)
    scaler = dataset.scaler

    n_total = len(dataset)
    if n_total < 100:
        raise ValueError(f"Замало зразків після побудови вікон: {n_total}")

    # --- Хронологічний спліт (останні 15% — валід) ---
    train_idx, val_idx = chronological_split(n_total, val_frac=0.15)
    train_ds = Subset(dataset, train_idx)
    val_ds = Subset(dataset, val_idx)

    pin = (device.type == "cuda")
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True, num_workers=num_workers, pin_memory=pin)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False, num_workers=num_workers, pin_memory=pin)

    model = SimpleCNN1D(in_ch=len(features), lookback=lookback).to(device)
    class_weights = make_class_weights(dataset, train_idx, device)
    criterion = nn.CrossEntropyLoss(weight=class_weights)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr)

    best_f1 = -1.0   # ТЕПЕР: це F1-macro
    best_pack = None
    best_val_report = ""
    no_improve = 0

    for epoch in range(1, epochs + 1):
        model.train()
        train_losses = []

        for xb, yb in train_loader:
            xb = xb.to(device, non_blocking=True)
            yb = yb.to(device, non_blocking=True)

            optimizer.zero_grad(set_to_none=True)
            logits = model(xb)
            loss = criterion(logits, yb)
            loss.backward()
            optimizer.step()
            train_losses.append(loss.item())

        # --- Валідація ---
        model.eval()
        all_preds, all_true = [], []
        with torch.no_grad():
            for xb, yb in val_loader:
                xb = xb.to(device, non_blocking=True)
                logits = model(xb)
                preds = torch.argmax(logits, dim=1).cpu().numpy()
                all_preds.append(preds)
                all_true.append(yb.numpy())

        all_preds = np.concatenate(all_preds) if len(all_preds) else np.array([])
        all_true = np.concatenate(all_true) if len(all_true) else np.array([])

        avg_loss = float(np.mean(train_losses)) if train_losses else float("nan")

        if all_true.size:
            acc = accuracy_score(all_true, all_preds)
            f1_bin = f1_score(all_true, all_preds, zero_division=0)
            f1_macro = f1_score(all_true, all_preds, average="macro", zero_division=0)
            bal_acc = balanced_accuracy_score(all_true, all_preds)
            p_rate = float((all_preds == 1).mean())
            cm = confusion_matrix(all_true, all_preds, labels=[0, 1])
            tn, fp, fn, tp = int(cm[0, 0]), int(cm[0, 1]), int(cm[1, 0]), int(cm[1, 1])
        else:
            acc = f1_bin = f1_macro = bal_acc = p_rate = 0.0
            tn = fp = fn = tp = 0

        tqdm.write(
            f"   [epoch {epoch:02d}] loss={avg_loss:.4f} | acc={acc:.4f} | "
            f"f1_bin={f1_bin:.4f} | f1m={f1_macro:.4f} | bal_acc={bal_acc:.4f} | "
            f"P-rate={p_rate:.2%} | tn={tn} fp={fp} fn={fn} tp={tp}"
        )
        logger.info(
            f"[epoch {epoch:02d}] loss={avg_loss:.4f} | acc={acc:.4f} | "
            f"f1_bin={f1_bin:.4f} | f1m={f1_macro:.4f} | bal_acc={bal_acc:.4f} | "
            f"P-rate={p_rate:.2%} | tn={tn} fp={fp} fn={fn} tp={tp}"
        )

        if all_true.size and len(np.unique(all_preds)) == 1:
            msg = "⚠️  Модель на валідації передбачає один клас на всіх зразках."
            tqdm.write(msg); logger.warning(msg)

        # --- Критерій покращення / рання зупинка (F1-macro) ---
        improved = f1_macro > (best_f1 + 1e-6)

        if improved:
            best_f1 = f1_macro  # зберігаємо саме F1-macro
            best_pack = {
                "model_state": model.state_dict(),
                "in_channels": len(features),
                "lookback": lookback,
                "features": features,
                "target_col": target_col,
                "created": datetime.utcnow().isoformat() + "Z",
            }
            try:
                rep = classification_report(all_true, all_preds, digits=4, zero_division=0)
            except Exception:
                rep = ""
            best_val_report = rep
            no_improve = 0
        else:
            no_improve += 1
            if no_improve >= patience:
                tqdm.write(f"   ⏹️ early stop at epoch {epoch} (best_f1m={best_f1:.4f})")
                logger.info(f"early_stop at epoch {epoch} (best_f1m={best_f1:.4f})")
                break

    return best_pack, scaler, best_f1, best_val_report


def save_artifacts(models_dir: Path, name: str, pack: dict, scaler: StandardScaler, logger: logging.Logger):
    """
    Зберігаємо:
      - torch-пакет моделі:  <name>.pt
      - scaler:              <name>.scaler.npz
      - специфікацію фіч:    <name>.spec.json
    """
    models_dir.mkdir(parents=True, exist_ok=True)

    # 1) torch state
    torch.save(pack, models_dir / f"{name}.pt")

    # 2) scaler
    mean_ = getattr(scaler, "mean_", None)
    scale_ = getattr(scaler, "scale_", None)
    var_ = getattr(scaler, "var_", None)
    if mean_ is not None and scale_ is not None:
        np.savez_compressed(
            models_dir / f"{name}.scaler.npz",
            mean_=np.asarray(mean_, dtype=np.float32),
            scale_=np.asarray(scale_, dtype=np.float32),
            var_=np.asarray(var_ if var_ is not None else np.square(scale_), dtype=np.float32),
        )

    # 3) spec.json
    spec = {
        "features": pack.get("features", []),
        "lookback": pack.get("lookback", None),
        "target_col": pack.get("target_col", None),
        "in_channels": pack.get("in_channels", None),
        "created": pack.get("created", None),
        "model_file": f"{name}.pt",
        "scaler_file": f"{name}.scaler.npz",
    }
    with open(models_dir / f"{name}.spec.json", "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)

    logger.info(f"💾 Збережено: {models_dir / (name + '.pt')}, "
                f"{models_dir / (name + '.scaler.npz')}, {models_dir / (name + '.spec.json')}")


# ===================== main =====================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--data-root", default="test/data",
                        help="Корінь із CSV для всіх символів (наприклад test/data)")
    parser.add_argument("--models-dir", default="models/CNN")
    parser.add_argument("--logs-dir", default="logs/CNN")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--patience", type=int, default=12, help="Early stopping: кількість підряд епох без покращення f1.")
    parser.add_argument("--min-delta", type=float, default=1e-3, help="(Не використовується для ES) Мінімальне покращення f1.")
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--cnn_input_col", default="close",
                        help="Колонка за замовчуванням, якщо варіант фіч не підходить")
    parser.add_argument("--cnn_lookback", type=int, default=64)
    parser.add_argument("--num-workers", type=int, default=0)
    args = parser.parse_args()

    # Пристрій
    if args.device == "auto":
        device_str = "cuda" if torch.cuda.is_available() else "cpu"
    elif args.device == "cuda" and not torch.cuda.is_available():
        print("⚠️  Обрано --device cuda, але CUDA недоступна. Перехід на CPU.")
        device_str = "cpu"
    else:
        device_str = args.device
    device = torch.device(device_str)

    symbol = args.symbol.upper()

    # Шляхи моделей/логів: models/<SYMBOL>/CNN і logs/<SYMBOL>/CNN
    models_dir = Path(args.models_dir)
    if models_dir.as_posix().replace("\\", "/").rstrip("/").lower() == "models/cnn":
        models_dir = Path("models") / symbol / "CNN"

    logs_dir = Path(args.logs_dir)
    if logs_dir.as_posix().replace("\\", "/").rstrip("/").lower() == "logs/cnn":
        logs_dir = Path("logs") / symbol / "CNN"

    # Логер
    logger = setup_logger(logs_dir)

    # ==== Знаходимо теку з даними для символу ====
    base_root = Path(args.data_root)
    symbol_dir = None
    if base_root.name.upper() == symbol:
        symbol_dir = base_root
    elif (base_root / symbol).exists():
        symbol_dir = base_root / symbol
    else:
        try:
            for p in base_root.iterdir():
                if p.is_dir() and p.name.upper() == symbol:
                    symbol_dir = p
                    break
        except FileNotFoundError:
            pass
        if symbol_dir is None:
            symbol_dir = base_root / symbol

    data_root = symbol_dir  # тека символу

    header = (f"🧩 Settings: symbol={symbol} | data_dir={data_root} | models_dir={models_dir} | "
              f"logs_dir={logs_dir} | device={device_str} | epochs={args.epochs} | "
              f"patience={args.patience} | min_delta={args.min_delta}")
    print(header); logger.info(header)

    seed_everything(42)

    # ===== Варіанти фіч =====
    df_sample = None
    try:
        sample_path = find_training_csv(data_root, symbol, TIMEFRAMES[0], TARGETS[0])
        df_sample = pd.read_csv(sample_path, nrows=100)
    except Exception:
        pass

    variant_sets = normalize_variant_sets(VARIANT_FEATURE_SETS, args.cnn_input_col, df_sample)
    print(f"🧩 Завантажено варіантів: {len(variant_sets)}"); logger.info(f"Варіантів фіч: {len(variant_sets)}")

    total = 0
    ok = 0
    pbar = tqdm(total=len(TIMEFRAMES) * len(TARGETS) * len(variant_sets),
                desc="🧠 Training CNN models", ncols=100)

    for tf in TIMEFRAMES:
        for target in TARGETS:
            target_col = f"target_{target}"

            # знайти CSV
            try:
                csv_path = find_training_csv(data_root, symbol, tf, target)
            except FileNotFoundError as e:
                msg = f"[CNN_{tf}_{target}] ⚠️ {e}"
                tqdm.write(msg); logger.warning(msg)
                total += len(variant_sets)              # враховуємо як спроби (скіпи)
                pbar.update(len(variant_sets))
                continue

            # читання CSV
            try:
                df = pd.read_csv(csv_path)
            except Exception as e:
                msg = f"[CNN_{tf}_{target}] ❌ error: {e}"
                tqdm.write(msg); logger.error(msg)
                total += len(variant_sets)
                pbar.update(len(variant_sets))
                continue

            for vidx, vset in enumerate(variant_sets, start=1):
                model_name = f"CNN_{tf}_{target}_V{vidx}"
                # ⏭️ Skip if all artifacts already exist
                pt_path = models_dir / f"{model_name}.pt"
                sc_path = models_dir / f"{model_name}.scaler.npz"
                spec_path = models_dir / f"{model_name}.spec.json"
                if pt_path.exists() and sc_path.exists() and spec_path.exists():
                    msg = f"[{model_name}] ⏭️ already exists — skipped"
                    tqdm.write(msg); logger.info(msg)
                    total += 1
                    pbar.update(1)
                    continue
                try:
                    features = pick_features(df, vset, target_col, args.cnn_input_col)
                    tqdm.write(f"[{model_name}] 🚀 training on {csv_path.name} | feats={len(features)} | lookback={args.cnn_lookback}")
                    logger.info(f"{model_name}: START | file={csv_path.name} | feats={features} | lookback={args.cnn_lookback}")

                    pack, scaler, best_f1, best_rep = train_one_model(
                        df=df,
                        features=features,
                        target_col=target_col,
                        lookback=args.cnn_lookback,
                        device=device,
                        logger=logger,
                        epochs=args.epochs,
                        batch_size=args.batch_size,
                        lr=args.lr,
                        num_workers=args.num_workers,
                        patience=args.patience,
                        min_delta=args.min_delta,
                    )

                    save_artifacts(models_dir, model_name, pack, scaler, logger)
                    tqdm.write(f"[{model_name}] ✅ trained | f1m={best_f1:.4f}")
                    logger.info(f"{model_name}: DONE | best_f1m={best_f1:.4f}")
                    if best_rep:
                        logger.info(f"{model_name}: validation report\n{best_rep}")

                    ok += 1
                except Exception as e:
                    msg = f"[{model_name}] ❌ error: {e}"
                    tqdm.write(msg); logger.error(msg)
                finally:
                    total += 1
                    pbar.update(1)

    pbar.close()
    tail = f"🧠 Training CNN models: {ok}/{total} успішно"
    print(tail); logger.info(tail)


if __name__ == "__main__":
    main()
