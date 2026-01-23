# tools/inspect_pt.py
import torch, sys, os
from collections import OrderedDict

path = sys.argv[1] if len(sys.argv) > 1 else "models/CNN/CNN_1d_long_V1.pt"
obj = torch.load(path, map_location="cpu")
print(f"Loaded type: {type(obj)}")
if isinstance(obj, (dict, OrderedDict)):
    keys = list(obj.keys())
    print(f"state_dict keys: {len(keys)}")
    # друк розмірів останніх 5 шарів
    for k in keys[-10:]:
        v = obj[k]
        if hasattr(v, "shape"):
            print(f"{k}: {tuple(v.shape)}")
    # евристика виходу
    out_dims = [tuple(obj[k].shape)[0] for k in keys if k.endswith("weight") and len(obj[k].shape)==2]
    if out_dims:
        print("possible out_dims (last linear weight rows):", out_dims[-3:])
else:
    try:
        sd = obj.state_dict()
        print("Module with state_dict keys:", len(sd))
    except Exception as e:
        print("No state_dict:", e)
