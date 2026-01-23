# test/start_train.py
# Unified pipeline: download → indicators → targets → train (all models)
# 1m: за замовчуванням тільки завантаження (індикатори/таргети пропускаємо)
# + Прискорене завантаження: requests.Session з пулом з'єднань, швидкий gzip-запис, регульований throttle
# + Скіп етапів за наявністю вихідних файлів (з прапорцями --force-*)
# + NEW: у stage_train моделі, що краще працюють на GPU, запускаються з CUDA (авто/за прапорцем)
# + NEW2: TIMEFRAMES/TARGETS/VERSIONS підтягуємо з test/Models/PPO/configs.py

import sys, os, importlib, importlib.util, subprocess, gzip, time, argparse, asyncio, threading, inspect
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Callable, Tuple, Dict

import numpy as np
import pandas as pd
from tqdm import tqdm  # прогрес-бар (Stage 1 і стадійні кроки)

# ── Шляхи ─────────────────────────────────────────────────────────────────────
THIS = Path(__file__).resolve()           # .../test/start_train.py
ROOT = THIS.parents[1]                    # корінь проєкту (рівень вище за 'test')
for p in {str(ROOT), str(THIS.parent)}:   # додаємо і корінь, і папку test
    if p not in sys.path:
        sys.path.insert(0, p)

# ── Надійне підвантаження get_klines з різних місць + швидкий HTTP-fallback ─
def resolve_get_klines():
    """
    Повертає async-функцію get_klines(...) **суто** з binance_conn.*.
    Жодних лімітів/затримок тут НЕМАЄ. Жодного HTTP-fallback немає.

    Всі обмеження (limit, retry, anti-429 тощо) повинні бути реалізовані
    тільки в binance_conn.py.
    """
    import asyncio
    import importlib.util
    import inspect
    from pathlib import Path

    ROOT = Path(__file__).resolve().parent
    THIS = Path(__file__).resolve()

    # 1) Спробувати стандартні імпорти модулів
    try:
        from binance_connector.binance_conn import get_klines as gk
        target = gk
    except Exception:
        target = None

    if target is None:
        try:
            from binance_connector.binance_conn import get_klines as gk
            target = gk
        except Exception:
            target = None

    # 2) Якщо не знайшли — спробувати завантажити з локальних файлів
    if target is None:
        candidates = [
            ROOT / "binance_conn.py",
            ROOT / "binance_connector" / "binance_conn.py",
            THIS.parent / "binance_conn.py",
            THIS.parent / "binance_connector" / "binance_conn.py",
        ]
        for path in candidates:
            if path.exists():
                try:
                    spec = importlib.util.spec_from_file_location("binance_conn_dynamic", path)
                    mod = importlib.util.module_from_spec(spec)
                    assert spec.loader is not None
                    spec.loader.exec_module(mod)  # може кинути через відсутні залежності — ігноруємо
                    if hasattr(mod, "get_klines"):
                        target = getattr(mod, "get_klines")
                        break
                except Exception:
                    continue  # толерантно пропускаємо і йдемо далі

    # 3) Якщо так і не знайшли — фейлимося явно
    if target is None or not callable(target):
        raise ImportError(
            "Не знайдено функцію 'get_klines' у binance_conn.py. "
            "Переконайся, що binance_conn.py присутній і експортує get_klines."
        )

    # 4) Гарантуємо async-інтерфейс без жодних обмежень тут
    if inspect.iscoroutinefunction(target):
        return target

    async def _async_adapter(*args, **kwargs):
        # ЖОДНИХ лімітів/затримок тут — лише адаптація sync → async
        return await asyncio.to_thread(target, *args, **kwargs)

    return _async_adapter


# Єдиний вхід: беремо get_klines тільки через resolve_get_klines()
get_klines = resolve_get_klines()


# ── Резольвери для indicators / generate_targets (гнучкі імпорти) ─────────────
def _import_first_ok(mod_names: List[str]):
    for name in mod_names:
        try:
            return importlib.import_module(name)
        except Exception:
            continue
    return None

def _candidate_paths_for(filename: str) -> List[Path]:
    cands = [
        ROOT / filename,
        THIS.parent / filename,                 # test/<filename>
        ROOT / "test" / filename,
        ROOT / "test" / "scripts" / filename,
        ROOT / "scripts" / filename,
        ROOT / "src" / filename,
        ROOT / "utils" / filename,
        ROOT / "test" / "utils" / filename,
    ]
    if not any(p.exists() for p in cands):
        for base in [ROOT / "test", ROOT]:
            for p in base.rglob(filename):
                cands.append(p)
                break
    seen, uniq = set(), []
    for p in cands:
        if p and str(p) not in seen:
            uniq.append(p); seen.add(str(p))
    return uniq

def _resolve_function(module_name_candidates: List[str],
                      func_candidates: List[str],
                      file_name: str) -> Callable:
    mod = _import_first_ok(module_name_candidates)
    if mod:
        for fn in func_candidates:
            if hasattr(mod, fn):
                return getattr(mod, fn)
    for path in _candidate_paths_for(file_name):
        if path.exists():
            spec = importlib.util.spec_from_file_location(f"{path.stem}_dynamic", path)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)  # type: ignore
            for fn in func_candidates:
                if hasattr(m, fn):
                    return getattr(m, fn)
    mods_str = ", ".join(module_name_candidates)
    paths_str = ", ".join(str(p) for p in _candidate_paths_for(file_name))
    raise ModuleNotFoundError(
        f"Не вдалося знайти функції {func_candidates} у модулях [{mods_str}] або файлах: {paths_str}"
    )

add_critical_indicators = _resolve_function(
    module_name_candidates=["indicators","test.indicators","test.scripts.indicators","scripts.indicators","utils.indicators"],
    func_candidates=["add_critical_indicators","compute_critical_indicators","build_critical_indicators"],
    file_name="indicators.py",
)
add_targets_first_touch = _resolve_function(
    module_name_candidates=["generate_targets","test.generate_targets","test.scripts.generate_targets","scripts.generate_targets","utils.generate_targets"],
    func_candidates=["add_targets_first_touch","build_targets_first_touch","generate_targets_first_touch"],
    file_name="generate_targets.py",
)

# ── Хелпери: час, спінер ─────────────────────────────────────────────────────
def utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)

def dt_parse(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)

def ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)

def run_with_spinner(fn: Callable, desc: str):
    result_holder = {"value": None, "error": None}
    done_event = threading.Event()
    start_time = time.time()
    frames = "|/-\\"

    def worker():
        try:
            result_holder["value"] = fn()
        except BaseException as e:
            result_holder["error"] = e
        finally:
            done_event.set()

    def spinner():
        i = 0
        while not done_event.is_set():
            elapsed = time.time() - start_time
            msg = f"\r{desc} {frames[i % len(frames)]}  (elapsed {int(elapsed//60)}m {int(elapsed%60):02d}s)"
            print(msg, end="", flush=True)
            time.sleep(0.15)
            i += 1
        print("\r" + " " * 80 + "\r", end="", flush=True)

    t_worker = threading.Thread(target=worker, daemon=True)
    t_spin = threading.Thread(target=spinner, daemon=True)
    t_worker.start(); t_spin.start()
    t_worker.join(); t_spin.join()

    if result_holder["error"] is not None:
        raise result_holder["error"]
    return result_holder["value"]

# ── Інтервали (сек/бар) ──────────────────────────────────────────────────────
BINANCE_STEPS = {
    "1m": 60, "15m": 60*15, "30m": 60*30,
    "1h": 60*60, "4h": 4*60*60, "1d": 24*60*60,
}

# ── FS helpers ────────────────────────────────────────────────────────────────
def ensure_dir(p: str):
    os.makedirs(p, exist_ok=True)

def last_close_from_gz(path: str) -> Optional[int]:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            last = None
            for line in f:
                last = line
        if last:
            parts = last.strip().split(",")
            return int(parts[6])  # closeTime ms
    except Exception:
        return None
    return None

# ── Stage 1: Download (progress by requests + швидкий I/O) ──────────────────
async def fetch_chunk(symbol: str, interval: str, start_dt: datetime, limit: int) -> List[List]:
    rows = await get_klines(symbol, interval, start_time=start_dt, limit=limit)
    return rows or []

def _bars_between(a: datetime, b: datetime, step_sec: int) -> int:
    if b <= a:
        return 0
    return int((b - a).total_seconds() // step_sec) + 1

def download_interval(symbol: str, data_dir: str, interval: str, limit: int = 1000,
                      earliest: Optional[datetime] = None, throttle_s: float = 0.05,
                      show_progress: bool = True) -> int:
    """
    Дозавантажує свічки у {data_dir}/{symbol}_{interval}.csv.gz
    Повертає кількість доданих рядків.
    Прогрес показуємо за кількістю HTTP-запитів (чанків).
    """
    ensure_dir(data_dir)
    outfile = os.path.join(data_dir, f"{symbol}_{interval}.csv.gz")
    step = BINANCE_STEPS[interval]

    now_fixed = utcnow()
    last_ms = last_close_from_gz(outfile)
    if last_ms is not None:
        start_dt = ms_to_dt(last_ms) + timedelta(seconds=step)
    else:
        start_dt = earliest or dt_parse("2017-01-01")

    base_start_dt = start_dt
    total_bars = max(1, _bars_between(base_start_dt, now_fixed, step))
    total_requests = max(1, (total_bars + limit - 1) // limit)

    appended = 0
    req_done = 0
    pbar = tqdm(total=total_requests, unit="req", desc=f"  ↳ {interval}", leave=False) if show_progress else None

    while True:
        rows = asyncio.run(fetch_chunk(symbol, interval, start_dt, limit))
        if not rows:
            break

        # швидкий запис одним блоком у gzip (менше I/O викликів)
        block = "".join(",".join(map(str, r)) + "\n" for r in rows).encode("utf-8")
        mode = "ab" if os.path.exists(outfile) else "wb"
        with gzip.open(outfile, mode) as f:
            f.write(block)
        appended += len(rows)

        req_done += 1
        if pbar is not None:
            pbar.update(1)

        last_close_ms = int(rows[-1][6])
        last_close_dt = ms_to_dt(last_close_ms)

        # якщо близько до "тепер" — завершуємо
        if (now_fixed - last_close_dt).total_seconds() < step:
            break

        start_dt = last_close_dt + timedelta(seconds=step)
        time.sleep(throttle_s)

    if pbar is not None:
        if req_done < pbar.total:
            pbar.update(pbar.total - req_done)
        pbar.close()

    return appended

def stage_download(symbol: str, data_dir: str, intervals: List[str], start: Optional[str], limit: int,
                   show_progress: bool, throttle_s: float, force_download: bool) -> None:
    print(f"\n🧩 Stage 1/4 — Download bars for {symbol}")
    earliest = dt_parse(start) if start else None
    for itv in intervals:
        outfile = os.path.join(data_dir, f"{symbol}_{itv}.csv.gz")
        if os.path.exists(outfile) and not force_download:
            print(f"  ⏭ {itv}: raw already exists → skip download")
            continue
        print(f"  📥 {itv} ...")
        try:
            added = download_interval(symbol, data_dir, itv, limit=limit, earliest=earliest,
                                      throttle_s=throttle_s, show_progress=show_progress)
            print(f"    ✅ {itv}: +{added} rows")
        except Exception as e:
            print(f"    ❌ {itv}: {type(e).__name__}: {e}")

# ── Stage 2: Indicators (1m — skip за замовчуванням) ─────────────────────────
RAW_COLS = [
    "open_time","open","high","low","close","volume",
    "close_time","quote_asset_volume","number_of_trades",
    "taker_buy_base_volume","taker_buy_quote_volume","ignore"
]

def need_build_indicators(raw_gz: str, out_csv: str, force_indicators: bool) -> bool:
    """
    Рахуємо лише якщо файлу немає АБО явно задано --force-indicators.
    """
    if force_indicators:
        return True
    return not os.path.exists(out_csv)

def _call_add_indicators_with_optional_progress(df: pd.DataFrame) -> pd.DataFrame:
    sig = None
    try: sig = inspect.signature(add_critical_indicators)
    except Exception: sig = None

    def just_call():
        return add_critical_indicators(df)

    if sig:
        params = sig.parameters
        if "progress" in params:
            with tqdm(total=100, desc="     calc indicators", leave=False) as p:
                def _set(v): p.n = max(0, min(int(v), 100)); p.refresh()
                return add_critical_indicators(df, progress=_set)
        if "progress_callback" in params:
            with tqdm(total=100, desc="     calc indicators", leave=False) as p:
                def _cb(v): p.n = max(0, min(int(v), 100)); p.refresh()
                return add_critical_indicators(df, progress_callback=_cb)

    return run_with_spinner(just_call, "     calc indicators")

def build_indicators_for_file(raw_gz: str, out_csv: str) -> int:
    df = run_with_spinner(lambda: pd.read_csv(raw_gz, header=None, compression="gzip"), "     read raw")
    df.columns = RAW_COLS
    for c in ["open","high","low","close","volume","quote_asset_volume","taker_buy_base_volume","taker_buy_quote_volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = _call_add_indicators_with_optional_progress(df)
    run_with_spinner(lambda: df.to_csv(out_csv, index=False), "     write indicators")
    return len(df)

def stage_indicators(symbol: str, data_dir: str, intervals: List[str], skip_1m: bool, force_indicators: bool) -> None:
    print(f"\n🧩 Stage 2/4 — Indicators for {symbol}")
    for itv in intervals:
        if skip_1m and itv == "1m":
            print("  ⏭ 1m: skip (download-only policy)")
            continue
        raw = os.path.join(data_dir, f"{symbol}_{itv}.csv.gz")
        if not os.path.exists(raw):
            print(f"  ⚠️ no raw file for {itv}, skip")
            continue
        out_csv = os.path.join(data_dir, f"{symbol}_{itv}_critical_indicators.csv")
        try:
            if need_build_indicators(raw, out_csv, force_indicators):
                print(f"  🧮 {itv}:")
                n = build_indicators_for_file(raw, out_csv)
                print(f"     done → {os.path.basename(out_csv)} ({n} rows)")
            else:
                print(f"  ⏭ {itv}: indicators exist → skip")
        except Exception as e:
            print(f"  ❌ {itv}: {type(e).__name__}: {e}")

# ── Stage 3: Targets (1m — skip за замовчуванням) ────────────────────────────
def need_build_targets(inp_csv: str, out_long: str, out_short: str, force_targets: bool) -> bool:
    """
    Рахуємо лише якщо НЕМАЄ обох вихідних файлів АБО явно --force-targets.
    """
    if force_targets:
        return True
    return not (os.path.exists(out_long) and os.path.exists(out_short))

def _call_add_targets_with_optional_progress(df: pd.DataFrame, **kwargs) -> pd.DataFrame:
    sig = None
    try: sig = inspect.signature(add_targets_first_touch)
    except Exception: sig = None

    def just_call():
        return add_targets_first_touch(df, **kwargs)

    if sig:
        params = sig.parameters
        if "progress" in params:
            with tqdm(total=100, desc="     calc targets", leave=False) as p:
                def _set(v): p.n = max(0, min(int(v), 100)); p.refresh()
                return add_targets_first_touch(df, progress=_set, **kwargs)
        if "progress_callback" in params:
            with tqdm(total=100, desc="     calc targets", leave=False) as p:
                def _cb(v): p.n = max(0, min(int(v), 100)); p.refresh()
                return add_targets_first_touch(df, progress_callback=_cb, **kwargs)

    return run_with_spinner(just_call, "     calc targets")

def build_targets_for_file(inp_csv: str, out_dir: str,
                           atr_period=14, atr_kind="wilder",
                           tp_mult=0.5, sl_mult=1.0, lookahead=10, fee=0.0005) -> None:
    df = run_with_spinner(lambda: pd.read_csv(inp_csv), "     read indicators")
    df.columns = [c.strip().lower() for c in df.columns]
    df = _call_add_targets_with_optional_progress(
        df,
        atr_period=atr_period,
        atr_kind=atr_kind,
        atr_multiplier_tp=tp_mult,
        atr_multiplier_sl=sl_mult,
        lookahead=lookahead,
        fee_buffer=fee,
    )
    base = os.path.splitext(os.path.basename(inp_csv))[0]
    out_all   = os.path.join(out_dir, f"{base}_with_targets_all.csv")
    out_long  = os.path.join(out_dir, f"{base}_with_targets_long.csv")
    out_short = os.path.join(out_dir, f"{base}_with_targets_short.csv")
    run_with_spinner(lambda: df.to_csv(out_all, index=False), "     write targets(all)")
    cols_common = [c for c in df.columns if c not in ["target_long","target_short"]]
    run_with_spinner(lambda: df[cols_common + ["target_long"]].to_csv(out_long, index=False), "     write targets(long)")
    run_with_spinner(lambda: df[cols_common + ["target_short"]].to_csv(out_short, index=False), "     write targets(short)")

def stage_targets(symbol: str, data_dir: str, intervals: List[str],
                  atr_period=14, atr_kind="wilder",
                  tp_mult=0.5, sl_mult=1.0, lookahead=10, fee=0.0005,
                  skip_1m: bool = True, force_targets: bool = False) -> None:
    print(f"\n🧩 Stage 3/4 — Targets for {symbol}")
    for itv in intervals:
        if skip_1m and itv == "1m":
            print("  ⏭ 1m: skip (download-only policy)")
            continue
        inp_csv = os.path.join(data_dir, f"{symbol}_{itv}_critical_indicators.csv")
        if not os.path.exists(inp_csv):
            print(f"  ⚠️ no indicators for {itv}, skip")
            continue
        out_long  = os.path.join(data_dir, f"{symbol}_{itv}_critical_indicators_with_targets_long.csv")
        out_short = os.path.join(data_dir, f"{symbol}_{itv}_critical_indicators_with_targets_short.csv")
        try:
            if need_build_targets(inp_csv, out_long, out_short, force_targets):
                print(f"  🎯 {itv}:")
                build_targets_for_file(inp_csv, data_dir, atr_period, atr_kind, tp_mult, sl_mult, lookahead, fee)
                print(f"     done")
            else:
                print(f"  ⏭ {itv}: targets exist → skip")
        except Exception as e:
            print(f"  ❌ {itv}: {type(e).__name__}: {e}")

# ── Configs loader (TIMEFRAMES/TARGETS/VERSIONS) ─────────────────────────────
def _load_configs_module():
    # 1) пробуємо імпорт як модуль
    try:
        return importlib.import_module("test.Models.PPO.configs")
    except Exception:
        pass
    # 2) пробуємо з файлу
    cfg_path = ROOT / "test" / "Models" / "PPO" / "configs.py"
    if cfg_path.exists():
        spec = importlib.util.spec_from_file_location("ppo_configs_fallback", str(cfg_path))
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)  # type: ignore
        return mod
    raise RuntimeError("Не вдалось імпортувати test/Models/PPO/configs.py")

def load_timeframes_targets_versions() -> Tuple[List[str], List[str], List[str]]:
    mod = _load_configs_module()
    TIMEFRAMES = list(getattr(mod, "TIMEFRAMES"))
    TARGETS    = list(getattr(mod, "TARGETS"))
    VFS        = dict(getattr(mod, "VARIANT_FEATURE_SETS"))
    keys = sorted(int(k) for k in VFS.keys())
    VERSIONS = [f"V{k}" for k in keys]
    return TIMEFRAMES, TARGETS, VERSIONS

# ── Stage 4: Training ────────────────────────────────────────────────────────
def run_subprocess_module(module: str, args: List[str], env: Optional[Dict[str,str]] = None) -> int:
    cmd = [sys.executable, "-m", module] + args
    print("   ▶", " ".join(cmd))
    return subprocess.call(cmd, env=env)

def run_with_fallback(tag: str, module: str, base_args: List[str], extra_args: List[str], env: Dict[str,str]) -> int:
    """
    1) Перший запуск: base + extra (передаємо --timeframes/--modes/--versions)
    2) Якщо RC != 0 — повтор без extra (сумісність із старими тренерами)
    """
    rc = run_subprocess_module(module, base_args + extra_args, env=env)
    if rc == 0:
        return rc
    print(f"   ⚠️ {tag}: повтор без додаткових аргументів (--timeframes/--modes/--versions)")
    return run_subprocess_module(module, base_args, env=env)

def torch_cuda_available() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False

def choose_device(device_pref: Optional[str]) -> str:
    """Вертає 'cuda' якщо device_pref='cuda' або якщо CUDA доступна; інакше 'cpu'."""
    if device_pref:
        return device_pref
    return "cuda" if torch_cuda_available() else "cpu"

def stage_train(symbol: str, device_pref: Optional[str],
                cfg_timeframes: List[str], cfg_targets: List[str], cfg_versions: List[str]) -> None:
    print(f"\n🧩 Stage 4/4 — Train models for {symbol}")
    # ENV для тренерів (раптом читають з оточення)
    env = os.environ.copy()
    env.update({
        "MB_TIMEFRAMES": ",".join(cfg_timeframes),
        "MB_MODES": ",".join(cfg_targets),
        "MB_VERSIONS": ",".join(cfg_versions),
    })

    # Додаткові аргументи (нові тренери)
    extra = [
        "--timeframes", ",".join(cfg_timeframes),
        "--modes",      ",".join(cfg_targets),
        "--versions",   ",".join(cfg_versions),
    ]

    # ── CNN → GPU якщо є (конволюції)
    cnn_device = choose_device(device_pref)
    rc = run_subprocess_module(
        "test.Models.CNN.train_cnn_crypto",
        ["--symbol", symbol, "--device", cnn_device],
        env=env
    )
    if rc != 0:
        print("   ⚠️ CNN trainer returned non-zero exit code.")

    # ── LSTM → GPU якщо є
    lstm_device = choose_device(device_pref)
    rc = run_subprocess_module(
        "test.Models.LSTM.train_lstm_crypto",
        ["--symbol", symbol, "--device", lstm_device],
        env=env
    )
    if rc != 0:
        print("   ⚠️ LSTM trainer returned non-zero exit code.")

    # ── PPO → GPU для політики/критика (з fallback)
    ppo_device = choose_device(device_pref)
    rc = run_with_fallback(
        "PPO",
        "test.Models.PPO.ppo_train",
        ["--symbol", symbol, "--device", ppo_device],
        extra_args=extra,
        env=env
    )
    if rc != 0:
        print("   ⚠️ PPO trainer returned non-zero exit code.")

    # ── DQN → GPU якщо є (з fallback)
    dqn_device = choose_device(device_pref)
    rc = run_with_fallback(
        "DQN",
        "test.Models.DQN.train_all_dqn",
        ["--symbol", symbol, "--device", dqn_device],
        extra_args=extra,
        env=env
    )
    if rc != 0:
        print("   ⚠️ DQN trainer returned non-zero exit code.")

    # ── Transformers → GPU якщо є (ймовірно не потребують tf/targets; даємо лише env)
    tr_device = choose_device(device_pref)
    rc = run_subprocess_module(
        "test.Models.Transformer.train_all_transformers",
        ["--symbol", symbol, "--device", tr_device],
        env=env
    )
    if rc != 0:
        print("   ⚠️ Transformer trainer returned non-zero exit code.")

    # ── XGBoost → --gpu якщо CUDA доступна/попросили
    xgb_use_gpu = (choose_device(device_pref) == "cuda")
    xgb_args = ["--symbol", symbol] + (["--gpu"] if xgb_use_gpu else [])
    rc = run_subprocess_module("test.Models.XGBoost.train_all_xgboost", xgb_args, env=env)
    if rc != 0:
        print("   ⚠️ XGBoost trainer returned non-zero exit code.")

    # ── QLearning (табличний) → CPU
    rc = run_subprocess_module("test.Models.QLearning.train_all_qlearning", ["--symbol", symbol], env=env)
    if rc != 0:
        print("   ⚠️ QLearning trainer returned non-zero exit code.")

# ── CLI ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Unified pipeline: download → indicators → targets → train (all models)")
    ap.add_argument("--symbol", default="BTCUSDT", help="e.g., BTCUSDT / ETHUSDT")

    # NEW: якщо не передано --intervals, беремо TIMEFRAMES з configs.py
    ap.add_argument("--intervals", default=None,
                    help="comma-separated list (e.g. 15m,1h,4h,1d). "
                         "If omitted, TIMEFRAMES from test/Models/PPO/configs.py will be used.")

    ap.add_argument("--start", default=None, help="earliest start date YYYY-MM-DD (if files absent), default=2017-01-01")
    ap.add_argument("--limit", type=int, default=1000, help="max klines per request")
    ap.add_argument("--throttle", type=float, default=0.05, help="delay between requests in seconds (Stage 1)")
    # скіпи/форси
    ap.add_argument("--force-download", action="store_true", help="перекачати навіть якщо raw існує")
    ap.add_argument("--force-indicators", action="store_true", help="перерахувати індикатори навіть якщо файл існує")
    ap.add_argument("--force-targets", action="store_true", help="перерахувати таргети навіть якщо файли існують")
    # 1m індикатори/таргети — пропуск за замовчуванням
    ap.add_argument("--skip-1m-ind", dest="skip_1m_ind", action="store_true", default=True, help="skip indicators for 1m (default)")
    ap.add_argument("--no-skip-1m-ind", dest="skip_1m_ind", action="store_false", help="do not skip indicators for 1m")
    ap.add_argument("--skip-1m-targets", dest="skip_1m_targets", action="store_true", default=True, help="skip targets for 1m (default)")
    ap.add_argument("--no-skip-1m-targets", dest="skip_1m_targets", action="store_false", help="do not skip targets for 1m")
    # тренування
    ap.add_argument("--device", choices=["cpu","cuda"], default=None, help="preferred device for supported trainers")
    ap.add_argument("--atr-period", type=int, default=14)
    ap.add_argument("--atr-kind", default="wilder", choices=["wilder","sma","ema"])
    ap.add_argument("--tp-mult", type=float, default=0.5)
    ap.add_argument("--sl-mult", type=float, default=1.0)
    ap.add_argument("--lookahead", type=int, default=10)
    ap.add_argument("--fee", type=float, default=0.0005)
    ap.add_argument("--no-progress", action="store_true", help="Disable download progress bar (Stage 1)")
    args = ap.parse_args()

    # підтягуємо TIMEFRAMES/TARGETS/VERSIONS з configs.py
    cfg_timeframes, cfg_targets, cfg_versions = load_timeframes_targets_versions()
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | INFO | configs.py -> "
          f"TIMEFRAMES={cfg_timeframes} TARGETS={cfg_targets} VERSIONS={cfg_versions}")

    symbol = args.symbol.upper()
    data_dir = os.path.join("test", "data", symbol)
    ensure_dir(data_dir)

    # Інтервали: якщо --intervals не задано → беремо з configs.py
    if args.intervals is None or not str(args.intervals).strip():
        intervals = list(cfg_timeframes)
        print(f"   ➜ using TIMEFRAMES from configs.py: {intervals}")
    else:
        intervals = [s.strip() for s in args.intervals.split(",") if s.strip()]
        print(f"   ➜ using TIMEFRAMES from --intervals: {intervals}")

    # 1) download (пропускаємо, якщо raw існує і не --force-download)
    stage_download(symbol, data_dir, intervals, start=args.start, limit=args.limit,
                   show_progress=not args.no_progress, throttle_s=args.throttle,
                   force_download=args.force_download)

    # 2) indicators (1m пропускаємо за замовч., а також скіпимо якщо файл існує і не --force-indicators)
    stage_indicators(symbol, data_dir, intervals, skip_1m=args.skip_1m_ind, force_indicators=args.force_indicators)

    # 3) targets    (1m пропускаємо за замовч., а також скіпимо якщо обидва файли існують і не --force-targets)
    stage_targets(
        symbol, data_dir, intervals,
        atr_period=args.atr_period, atr_kind=args.atr_kind,
        tp_mult=args.tp_mult, sl_mult=args.sl_mult,
        lookahead=args.lookahead, fee=args.fee,
        skip_1m=args.skip_1m_targets, force_targets=args.force_targets
    )

    # 4) training (тут уже також передаємо timeframes/targets/versions у тренери з fallback)
    stage_train(symbol, device_pref=args.device,
                cfg_timeframes=cfg_timeframes, cfg_targets=cfg_targets, cfg_versions=cfg_versions)

    print("\n✅ Pipeline done.")

if __name__ == "__main__":
    main()

