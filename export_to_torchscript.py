import os
import torch
import importlib

# ==== Налаштуй під себе ====
MODELS_DIR = "models/CNN"           # де лежать твої .pt зі state_dict
PATTERN = "CNN_15m_"                # фільтр імен
OUT_DIR = "models/CNN_torchscript"  # куди зберігати готові моделі
CLASS_PATH = "test.Models.CNN.arch:CNNCrypto"  # МОДУЛЬ:КЛАС з твого тренувального коду
INIT_KWARGS = {"in_channels": 61, "lookback": 64, "out_dim": 1}
EXAMPLE_INPUT_SHAPE = (1, 61, 64)   # (B, C, L) або під свою модель
# ===========================

os.makedirs(OUT_DIR, exist_ok=True)

module_path, class_name = CLASS_PATH.split(":")
ModelClass = getattr(importlib.import_module(module_path), class_name)

def try_load(path):
    try:
        return torch.load(path, map_location="cpu")
    except TypeError:
        return torch.load(path, map_location="cpu")

def get_state_dict(obj):
    if isinstance(obj, dict):
        for k in ["state_dict","model_state_dict","net","weights","params","ema"]:
            if k in obj and isinstance(obj[k], dict):
                return obj[k]
        if all(isinstance(v, torch.Tensor) for v in obj.values()):
            return obj
    if hasattr(obj, "state_dict"):
        return obj.state_dict()
    return None

for fn in sorted(os.listdir(MODELS_DIR)):
    if not fn.endswith(".pt") and not fn.endswith(".pth"):
        continue
    if PATTERN and PATTERN not in fn:
        continue

    src = os.path.join(MODELS_DIR, fn)
    dst = os.path.join(OUT_DIR, fn)

    ckpt = try_load(src)
    sd = get_state_dict(ckpt)
    if sd is None:
        print(f"⚠️ {fn}: не знайшов state_dict — пропускаю")
        continue

    # прибрати префікс 'module.' якщо був тренінг в DDP
    if any(k.startswith("module.") for k in sd):
        sd = { (k[7:] if k.startswith("module.") else k): v for k,v in sd.items() }

    model = ModelClass(**INIT_KWARGS).eval()
    model.load_state_dict(sd, strict=False)

    # Прикладний тензор для trace/script — підлаштуй, якщо у тебе інший формат
    example = torch.randn(*EXAMPLE_INPUT_SHAPE)
    try:
        scripted = torch.jit.trace(model, example)
    except Exception:
        scripted = torch.jit.script(model)

    scripted.save(dst)
    print(f"✅ Saved TorchScript: {dst}")

print("Done.")
