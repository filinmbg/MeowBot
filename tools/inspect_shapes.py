import torch
from collections import OrderedDict

path = r"models/CNN/CNN_1d_long_V1.pt"
obj = torch.load(path, map_location="cpu")

sd = obj if isinstance(obj, (dict, OrderedDict)) else obj.state_dict()

for k in ["conv2.weight","conv2.bias","fc1.weight","fc1.bias","fc2.weight","fc2.bias"]:
    if k in sd:
        v = sd[k]
        print(f"{k}: {tuple(v.shape)}")

print("\nAll weight keys:")
for k, v in sd.items():
    if hasattr(v, "shape") and k.endswith("weight"):
        print(f"{k}: {tuple(v.shape)}")
