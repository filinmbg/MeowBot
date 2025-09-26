# -*- coding: utf-8 -*-
"""
Конвертація чекпоінтів CNN у формат {'model': nn.Module} для бектесту.
Підтримує:
 - TorchScript (копіює як є)
 - повний nn.Module
 - dict['model'] (nn.Module)
 - state_dict на будь-якій глибині (вміє знімати 'module.' префікс)
Авто-інфер:
 - in_channels із форми ваг conv2.weight -> shape[1]
 - out_dim із форми ваг fc2.weight -> shape[0]
"""

import os
import sys
import shutil
import importlib
import importlib.util
from typing import Optional, Dict, Any, Tuple, List

import torch
import torch.nn as nn
import pandas as pd
import numpy as np


# =========================
#           SETTINGS
# =========================
MODELS_DIR = "models/CNN"       # звідки брати .pt/.pth
PATTERN = "CNN_15m_"            # підрядок у назві; "" щоб брати всі
OUT_DIR = "models/CNN_packed"   # куди класти упаковані моделі
OVERWRITE = True

# Вкажи клас моделі: через файл (пріоритетно) або пакет
CLASS_FILE = r"test\Models\CNN\arch.py:CNNCrypto"   # <-- ми створили цей клас
CLASS_PATH = ""  # "test.Models.CNN.arch:CNNCrypto" якщо хочеш імпорт як пакет

# Значення за замовчуванням (будуть ПЕРЕЗАТЕРТІ, якщо знайдемо їх у вагах)
DEFAULT_IN_CHANNELS = 61
DEFAULT_LOOKBACK = 64
DEFAULT_OUT_DIM = 1

# CSV для інформації (не обов’язково, тут тільки для логів)
INFER_IN_CHANNELS_FROM_CSV = "test/data/BTCUSDT/BTCUSDT_15m_critical_indicators.csv"
# =========================


# -------- import helpers --------
def ensure_project_root_on_syspath():
    root = os.getcwd()
    if root not in sys.path:
        sys.path.insert(0, root)


def dynamic_import_from_file(file_spec: str) -> type:
    file_path, class_name = file_spec.split(":", 1)
    file_path = os.path.normpath(file_path)
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"CLASS_FILE не знайдено: {file_path}")
    module_name = os.path.splitext(os.path.basename(file_path))[0]
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Не вдалося створити spec для {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)  # type: ignore
    return getattr(module, class_name)


def dynamic_import_from_module_path(class_path: str) -> type:
    module_path, class_name = class_path.split(":", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def load_model_class() -> type:
    ensure_project_root_on_syspath()
    if CLASS_FILE:
        return dynamic_import_from_file(CLASS_FILE)
    if CLASS_PATH:
        return dynamic_import_from_module_path(CLASS_PATH)
    raise RuntimeError("Не вказано CLASS_FILE/CLASS_PATH у SETTINGS")


# -------- state_dict helpers --------
def try_load_torchscript(path: str):
    try:
        m = torch.jit.load(path, map_location="cpu")
        m.eval()
        return m
    except Exception:
        return None


def try_torch_load(path: str):
    try:
        return torch.load(path, map_location="cpu", weights_only=True)
    except TypeError:
        return torch.load(path, map_location="cpu")


def is_state_dict_like(obj: Any) -> bool:
    return isinstance(obj, dict) and any(torch.is_tensor(v) for v in obj.values())


def flatten_dict_paths(d: Dict[str, Any], prefix: str = "") -> List[Tuple[str, Any]]:
    out = []
    for k, v in d.items():
        p = f"{prefix}.{k}" if prefix else k
        out.append((p, v))
        if isinstance(v, dict):
            out.extend(flatten_dict_paths(v, p))
    return out


def find_state_dict_anywhere(obj: Any) -> Optional[Dict[str, Any]]:
    if isinstance(obj, dict):
        if is_state_dict_like(obj):
            return obj
        for key in ["state_dict", "model_state_dict", "model_state", "net", "weights", "params", "ema", "teacher", "student"]:
            if key in obj and isinstance(obj[key], dict) and is_state_dict_like(obj[key]):
                return obj[key]
        for _, sub in flatten_dict_paths(obj):
            if isinstance(sub, dict) and is_state_dict_like(sub):
                return sub
    return None


def strip_module_prefix(sd: Dict[str, Any]) -> Dict[str, Any]:
    if any(k.startswith("module.") for k in sd.keys()):
        return { (k[7:] if k.startswith("module.") else k): v for k, v in sd.items() }
    return sd


def infer_hparams_from_sd(sd: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    """Повертає (in_channels, out_dim), якщо їх можна визначити з форм ваг."""
    in_ch = None
    out_dim = None
    # conv2.weight: (64, in_ch, k)
    if "conv2.weight" in sd and torch.is_tensor(sd["conv2.weight"]) and sd["conv2.weight"].dim() == 3:
        in_ch = int(sd["conv2.weight"].shape[1])
    # fc2.weight: (out_dim, hidden)
    if "fc2.weight" in sd and torch.is_tensor(sd["fc2.weight"]) and sd["fc2.weight"].dim() == 2:
        out_dim = int(sd["fc2.weight"].shape[0])
    return in_ch, out_dim


# -------- misc --------
def count_numeric_features(csv_path: str) -> int:
    df = pd.read_csv(csv_path, nrows=2000)
    num = df.select_dtypes(include=[np.number]).copy()
    cols = [c for c in num.columns if c.lower() != "timestamp"]
    return len(cols)


# =========================
#            MAIN
# =========================
def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    if INFER_IN_CHANNELS_FROM_CSV and os.path.exists(INFER_IN_CHANNELS_FROM_CSV):
        try:
            feats = count_numeric_features(INFER_IN_CHANNELS_FROM_CSV)
            print(f"ℹ️  CSV має числових фіч: {feats}")
        except Exception as e:
            print(f"⚠️  Не вдалося порахувати фічі з CSV: {e}")

    files = [
        os.path.join(MODELS_DIR, f)
        for f in sorted(os.listdir(MODELS_DIR))
        if f.lower().endswith((".pt", ".pth")) and (PATTERN in f if PATTERN else True)
    ]
    if not files:
        print("⚠️  Не знайдено моделей")
        return

    ModelClass = load_model_class()

    ok = ts_count = full_count = sd_count = fail = 0

    for src in files:
        name = os.path.basename(src)
        dst = os.path.join(OUT_DIR, name)
        if not OVERWRITE and os.path.exists(dst):
            print(f"⏭️  Пропуск (існує): {name}")
            ok += 1
            continue

        # 1) TorchScript?
        ts = try_load_torchscript(src)
        if ts is not None:
            shutil.copy2(src, dst)
            ts_count += 1
            ok += 1
            print(f"✅ TorchScript: {name}")
            continue

        # 2) torch.load
        try:
            obj = try_torch_load(src)
        except Exception as e:
            print(f"❌ {name}: torch.load помилка: {e}")
            fail += 1
            continue

        # 2a) Повна модель
        if isinstance(obj, nn.Module):
            torch.save({"model": obj.eval()}, dst)
            full_count += 1
            ok += 1
            print(f"✅ nn.Module: {name}")
            continue

        # 2b) Словник / state_dict
        sd = None
        if isinstance(obj, dict):
            if "model" in obj and isinstance(obj["model"], nn.Module):
                torch.save({"model": obj["model"].eval()}, dst)
                full_count += 1
                ok += 1
                print(f"✅ dict['model']: {name}")
                continue
            sd = find_state_dict_anywhere(obj)

        if sd is None:
            print(f"❌ Непідтримуваний формат: {name}")
            fail += 1
            continue

        sd = strip_module_prefix(sd)
        inferred_in, inferred_out = infer_hparams_from_sd(sd)
        in_channels = inferred_in if inferred_in is not None else DEFAULT_IN_CHANNELS
        out_dim = inferred_out if inferred_out is not None else DEFAULT_OUT_DIM

        # створюємо модель з інференими параметрами
        try:
            model = ModelClass(in_channels=in_channels, lookback=DEFAULT_LOOKBACK, out_dim=out_dim)
        except Exception as e:
            print(f"❌ Не вдалося створити модель для {name}: {e}")
            fail += 1
            continue

        # вантажимо ваги
        try:
            missing, unexpected = model.load_state_dict(sd, strict=False)
            if missing:
                print(f"  ⚠️ missing keys ({len(missing)}): {missing[:6]}{'...' if len(missing)>6 else ''}")
            if unexpected:
                print(f"  ⚠️ unexpected keys ({len(unexpected)}): {unexpected[:6]}{'...' if len(unexpected)>6 else ''}")
        except Exception as e:
            print(f"❌ load_state_dict провалився для {name}: {e}")
            fail += 1
            continue

        # зберігаємо у форматі {'model': model}
        try:
            torch.save({"model": model.eval()}, dst)
            sd_count += 1
            ok += 1
            print(f"✅ state_dict -> упаковано ({in_channels}ch, out={out_dim}): {name}")
        except Exception as e:
            print(f"❌ Помилка збереження {name}: {e}")
            fail += 1

    print(f"\nГотово. OK={ok} | TorchScript={ts_count} | nn.Module={full_count} | state_dict={sd_count} | FAIL={fail}")
    print(f"📁 Вихідна тека: {OUT_DIR}")


if __name__ == "__main__":
    main()
