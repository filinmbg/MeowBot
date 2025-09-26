# nvml_monitor.py
# pip install psutil pynvml
import time, sys
try:
    import psutil
except Exception:
    psutil = None

try:
    import pynvml as nvml
    nvml.nvmlInit()
except Exception as e:
    print("NVML не доступний:", e)
    sys.exit(1)

def cpu_ram():
    if not psutil:
        return None, None
    return psutil.cpu_percent(interval=0.1), psutil.virtual_memory().percent

try:
    n = nvml.nvmlDeviceGetCount()
    while True:
        c, r = cpu_ram()
        if c is not None:
            print(f"CPU={c:5.1f}%  RAM={r:5.1f}%")
        lines = []
        for i in range(n):
            h = nvml.nvmlDeviceGetHandleByIndex(i)
            name = nvml.nvmlDeviceGetName(h)
            try:
                name = name.decode()
            except Exception:
                pass
            util = nvml.nvmlDeviceGetUtilizationRates(h)  # gpu%, memory%
            mem = nvml.nvmlDeviceGetMemoryInfo(h)         # bytes
            lines.append(
                f"  GPU{i}:{name} | gpu={util.gpu:3d}% | mem={util.memory:3d}% | vram={mem.used/1e9:.2f}/{mem.total/1e9:.2f} GB"
            )
        print("\n".join(lines))
        print("-"*80)
        time.sleep(1.0)
finally:
    try: nvml.nvmlShutdown()
    except Exception: pass
