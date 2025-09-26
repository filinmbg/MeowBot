import argparse
import os
import torch

def main():
    ap = argparse.ArgumentParser("Inspect .pt/.pth checkpoint contents")
    ap.add_argument("path", help="Path to .pt file or a directory")
    args = ap.parse_args()

    paths = []
    if os.path.isdir(args.path):
        for fn in sorted(os.listdir(args.path)):
            if fn.lower().endswith((".pt", ".pth")):
                paths.append(os.path.join(args.path, fn))
    else:
        paths = [args.path]

    for p in paths:
        print(f"\n=== {p} ===")
        try:
            obj = torch.load(p, map_location="cpu")
        except Exception as e:
            print(f"  ❌ torch.load error: {e}")
            # Try TorchScript?
            try:
                ts = torch.jit.load(p, map_location="cpu")
                print("  ✅ TorchScript module detected")
                print(ts)
                continue
            except Exception as e2:
                print(f"  ❌ torch.jit.load error: {e2}")
                continue

        if hasattr(obj, "state_dict") and callable(getattr(obj, "state_dict")):
            print("  ✅ nn.Module detected (full model)")
            try:
                sd = obj.state_dict()
                print(f"  state_dict keys: {len(sd)}")
                for k, v in list(sd.items())[:5]:
                    print(f"    {k}: {tuple(v.shape)}")
            except Exception as e:
                print(f"  warn: can't read state_dict: {e}")
            continue

        if isinstance(obj, dict):
            keys = list(obj.keys())
            print(f"  dict keys: {keys}")
            if "model" in obj:
                m = obj["model"]
                if hasattr(m, "state_dict"):
                    print("  ✅ dict['model'] is nn.Module (full model)")
                    sd = m.state_dict()
                    print(f"  state_dict keys: {len(sd)}")
                    for k, v in list(sd.items())[:5]:
                        print(f"    {k}: {tuple(v.shape)}")
                else:
                    print("  ⚠️ dict['model'] exists but is not nn.Module")
            if "state_dict" in obj:
                sd = obj["state_dict"]
                if isinstance(sd, dict):
                    print(f"  ✅ state_dict entries: {len(sd)}")
                    for k, v in list(sd.items())[:8]:
                        try:
                            shp = tuple(v.shape)  # Tensor
                        except Exception:
                            shp = "?"
                        print(f"    {k}: {shp}")
                else:
                    print("  ⚠️ 'state_dict' is not a dict")
        else:
            print(f"  ⚠️ Unsupported object type: {type(obj)}")

if __name__ == "__main__":
    main()
