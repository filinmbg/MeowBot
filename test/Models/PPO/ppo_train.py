# -*- coding: utf-8 -*-
"""
PPO multi-runner with:
- single-line progress logs per model
- resume from *.last.zip + progress.json
- gymnasium-compatible env + Monitor (no gym.TimeLimit)
- adaptive parallel scaler:
    * periodic check every 2 minutes when load is below threshold
    * if load >= threshold -> next periodic check after 15 minutes
    * NO other checks (no instant retries, no backoffs)
    * after a job finishes — the parallel slot is CLOSED (limit decreases by 1)
    * cap at MAX_PARALLEL=20
Output artifacts per model:
    {models_dir}/{model}.zip
    {models_dir}/{model}.best.zip
    {models_dir}/{model}.last.zip
    {models_dir}/{model}.vecnorm.pkl}
    {models_dir}/{model}.spec.json
    {logs_dir}/{model}.csv
    {logs_dir}/{model}.monitor.csv (sb3 Monitor)
    {logs_dir}/{model}_progress.json
"""

from __future__ import annotations

import os, sys, json, time, math, argparse, traceback, threading, warnings
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Tuple, Dict, Any

import numpy as np
import pandas as pd

# ==== RL / Gym ====
import gymnasium as gym
from gymnasium import spaces

from stable_baselines3 import PPO
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.callbacks import BaseCallback, CallbackList
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize

# Silence SB3 CUDA warning for MLP policies
warnings.filterwarnings(
    "ignore",
    message=r"You are trying to run PPO on the GPU, but it is primarily intended to run on the CPU.*",
    category=UserWarning
)

# ==== Torch (for device info) ====
try:
    import torch
except Exception:
    torch = None

# ==== Utilization ====
try:
    import psutil
except Exception:
    psutil = None

try:
    import pynvml
    _NVML_OK = True
    pynvml.nvmlInit()
except Exception:
    _NVML_OK = False

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------

MAX_PARALLEL = 50

# Main threshold (norm) = 70%
# - below 0.70: it's okay to start more work (2-min cadence)
# - at/above 0.70: too busy (next check in 15 min)
UTIL_THRESHOLD = 0.70

CHECK_COOLDOWN_OK = 120    # 2 min
CHECK_COOLDOWN_HIGH = 900  # 15 min

# kept for compatibility (not used in the new flow)
START_ATTEMPT_BACKOFF = 300.0
START_LOG_COOLDOWN    = 5.0

TOTAL_UPDATES = 1000
STEPS_PER_UPDATE = 2048
TOTAL_TIMESTEPS = TOTAL_UPDATES * STEPS_PER_UPDATE  # 2_048_000

DEFAULT_TIMEFRAMES = ["1d"]
DEFAULT_MODES = ["long", "short"]
DEFAULT_VERSIONS = [f"V{i}" for i in range(1, 15+1)]  # V1..V15

# -----------------------------------------------------------------------------
# Paths / helpers
# -----------------------------------------------------------------------------

def ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def ensure_dir(p: str):
    os.makedirs(p, exist_ok=True)

def round2(x: float) -> float:
    return float(np.round(x, 3))

def model_name(symbol: str, tf: str, mode: str, version: str) -> str:
    # PPO_1d_long_V1_BTCUSDT
    return f"PPO_{tf}_{mode}_{version}_{symbol}"

# -----------------------------------------------------------------------------
# Simple trading env
# -----------------------------------------------------------------------------

EXCLUDE_COLS = {"open_time", "close_time", "target_long", "target_short"}

class SimpleTradingEnv(gym.Env):
    """
    Прості ознаки:
    - числові колонки + категоріальні/булеві кодуються (category codes / int)
    - нормування Z-score
    - епізод фіксованої довжини
    - Discrete(3): 0=short, 1=hold, 2=long
    """
    metadata = {"render_modes": []}

    def __init__(self, df: pd.DataFrame, mode: str, episode_len: int = 500):
        super().__init__()
        self.mode = mode

        df = df.copy()
        df.columns = [str(c).strip() for c in df.columns]

        base = df[[c for c in df.columns if c not in EXCLUDE_COLS]]

        num_df = base.select_dtypes(include=[np.number])

        cat_df = base.select_dtypes(include=["object", "category", "bool"])
        if not cat_df.empty:
            cat_encoded = pd.DataFrame(index=cat_df.index)
            for c in cat_df.columns:
                s = cat_df[c]
                if s.dtype == bool:
                    cat_encoded[c] = s.astype(int)
                else:
                    try:
                        cat = s.astype("category")
                        cat_encoded[c] = cat.cat.codes.replace(-1, 0)
                    except Exception:
                        cat_encoded[c] = pd.to_numeric(s, errors="coerce").fillna(0.0)
            feats = pd.concat([num_df, cat_encoded], axis=1)
        else:
            feats = num_df

        if feats.shape[1] == 0:
            raise RuntimeError(
                "Відсутні придатні для навчання фічі (усі колонки службові або нечислові)."
            )

        feats = feats.apply(pd.to_numeric, errors="coerce").fillna(0.0).astype(np.float32)

        arr = feats.values
        mean = arr.mean(axis=0, keepdims=True)
        std = arr.std(axis=0, ddof=0, keepdims=True)
        std[std == 0.0] = 1.0
        self.features = ((arr - mean) / std).astype(np.float32)

        if mode == "long" and "target_long" in df.columns:
            self.targets = df["target_long"].fillna(0.0).astype(float).values
        elif mode == "short" and "target_short" in df.columns:
            self.targets = df["target_short"].fillna(0.0).astype(float).values
        else:
            self.targets = np.zeros(len(df), dtype=np.float32)

        self.episode_len = int(episode_len)
        self.pos = 0
        self.step_in_episode = 0

        obs_dim = self.features.shape[1]
        self.observation_space = spaces.Box(low=-10, high=10, shape=(obs_dim,), dtype=np.float32)
        self.action_space = spaces.Discrete(3)

    def reset(self, *, seed: Optional[int]=None, options: Optional[dict]=None):
        super().reset(seed=seed)
        if len(self.features) <= self.episode_len + 2:
            self.pos = 0
        else:
            self.pos = np.random.randint(0, len(self.features) - self.episode_len - 1)
        self.step_in_episode = 0
        obs = self.features[self.pos]
        return obs.astype(np.float32), {}

    def step(self, action: int):
        t = float(self.targets[self.pos])
        if self.mode == "long":
            reward = (1.0 if action == 2 else 0.0) * t - (1.0 if action == 0 else 0.0) * (1.0 - t) * 0.1
        else:
            reward = (1.0 if action == 0 else 0.0) * t - (1.0 if action == 2 else 0.0) * (1.0 - t) * 0.1

        self.pos = min(self.pos + 1, len(self.features) - 1)
        self.step_in_episode += 1
        terminated = self.step_in_episode >= self.episode_len
        truncated = self.pos >= len(self.features) - 1

        obs = self.features[self.pos].astype(np.float32)
        return obs, float(reward), bool(terminated), bool(truncated), {}

# -----------------------------------------------------------------------------
# Data loading for env
# -----------------------------------------------------------------------------

def load_env_dataframe(symbol: str, timeframe: str, mode: str, data_dir: str) -> pd.DataFrame:
    fname = f"{symbol}_{timeframe}_critical_indicators_with_targets_{mode}.csv"
    path = os.path.join(data_dir, fname)
    if os.path.exists(path):
        return pd.read_csv(path)
    alt = os.path.join(data_dir, f"{symbol}_{timeframe}_critical_indicators.csv")
    if os.path.exists(alt):
        return pd.read_csv(alt)
    raise FileNotFoundError(f"Не знайдено дані для середовища: {path}")

def make_env(symbol: str, timeframe: str, mode: str, data_dir: str, logs_dir: str):
    def _thunk():
        df = load_env_dataframe(symbol, timeframe, mode, data_dir)
        env = SimpleTradingEnv(df, mode=mode, episode_len=500)
        env = Monitor(env, filename=None)
        return env
    vec = DummyVecEnv([_thunk])
    vec = VecNormalize(vec, norm_obs=True, norm_reward=True, clip_obs=10.0, clip_reward=10.0)
    return vec

# -----------------------------------------------------------------------------
# Utilization helpers
# -----------------------------------------------------------------------------

def get_gpu_utilization() -> float:
    if not _NVML_OK:
        return 0.0
    try:
        n = pynvml.nvmlDeviceGetCount()
        utils = []
        for i in range(n):
            h = pynvml.nvmlDeviceGetHandleByIndex(i)
            u = pynvml.nvmlDeviceGetUtilizationRates(h)
            utils.append(u.gpu / 100.0)
        if not utils:
            return 0.0
        return max(0.0, min(1.0, max(utils)))
    except Exception:
        return 0.0

def get_cpu_ram_utilization() -> Tuple[float, float]:
    cpu = psutil.cpu_percent(interval=0.2)/100.0 if psutil else 0.0
    ram = psutil.virtual_memory().percent/100.0 if psutil else 0.0
    return cpu, ram

def get_max_utilization() -> float:
    cpu, ram = get_cpu_ram_utilization()
    gpu = get_gpu_utilization()
    return max(cpu, ram, gpu)

# -----------------------------------------------------------------------------
# IO: progress/spec/log
# -----------------------------------------------------------------------------

def write_spec(models_dir: str, name: str, spec: Dict[str, Any]):
    path = os.path.join(models_dir, f"{name}.spec.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)

def read_progress(logs_dir: str, name: str) -> int:
    path = os.path.join(logs_dir, f"{name}_progress.json")
    if not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            j = json.load(f)
        return int(j.get("timesteps_done", 0))
    except Exception:
        return 0

def write_progress(logs_dir: str, name: str, steps: int):
    path = os.path.join(logs_dir, f"{name}_progress.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"timesteps_done": int(steps)}, f)

# -----------------------------------------------------------------------------
# Callbacks: Eval-lite + RolloutDiag
# -----------------------------------------------------------------------------

class CustomEvalCallback(BaseCallback):
    def __init__(self, eval_env, eval_freq: int = STEPS_PER_UPDATE, n_episodes: int = 5, verbose: int = 0):
        super().__init__(verbose)
        self.eval_env = eval_env
        self.eval_freq = int(max(1, eval_freq))
        self.n_episodes = int(max(1, n_episodes))
        self.last_mean_reward: float = 0.0
        self.last_std_reward: float  = float("nan")

    def _on_step(self) -> bool:
        return True

    def _on_training_start(self) -> None:
        pass

    def _on_step_end(self) -> None:
        pass

    def _on_rollout_start(self) -> None:
        pass

    def _on_training_end(self) -> None:
        pass

    def _on_step_training(self) -> None:
        pass

    def _on_step_rollout(self) -> None:
        pass

    def _init_callback(self) -> None:
        pass

    def _on_step_callback(self) -> None:
        pass

    def _on_step(self) -> bool:
        if self.num_timesteps % self.eval_freq != 0:
            return True

        old_training = None
        try:
            if isinstance(self.eval_env, VecNormalize):
                old_training = self.eval_env.training
                self.eval_env.training = False
        except Exception:
            pass

        ep_rewards = []
        for _ in range(self.n_episodes):
            obs = self.eval_env.reset()
            if isinstance(obs, tuple):
                obs = obs[0]
            done = False
            rsum = 0.0
            while not done:
                action, _ = self.model.predict(obs, deterministic=True)
                obs, reward, done, _ = self._step_eval_env(action)
                rsum += float(reward)
            ep_rewards.append(rsum)

        if ep_rewards:
            arr = np.asarray(ep_rewards, dtype=np.float32)
            self.last_mean_reward = float(arr.mean())
            self.last_std_reward  = float(arr.std())
        else:
            self.last_mean_reward, self.last_std_reward = 0.0, float("nan")

        try:
            if isinstance(self.eval_env, VecNormalize) and (old_training is not None):
                self.eval_env.training = old_training
        except Exception:
            pass
        return True

    def _step_eval_env(self, action):
        if np.isscalar(action):
            act = np.array([int(action)], dtype=np.int64)
        else:
            arr = np.asarray(action)
            act = arr.reshape(-1).astype(np.int64)
        obs, rewards, dones, infos = self.eval_env.step(act)
        reward = float(rewards[0] if isinstance(rewards, (list, np.ndarray)) else rewards)
        done   = bool(dones[0] if isinstance(dones, (list, np.ndarray)) else dones)
        return obs, reward, done, infos


class RolloutDiagCallback(BaseCallback):
    def __init__(self, model_name: str):
        super().__init__()
        self.model_name = model_name

    def _init_callback(self) -> None:
        pass

    def _on_step(self) -> bool:
        return True

    def _on_rollout_end(self) -> None:
        try:
            acts = np.array(self.model.rollout_buffer.actions).reshape(-1)
            acts = acts.astype(int)
            cnts = np.bincount(acts, minlength=3)
            p = cnts / max(1, cnts.sum())
            ent = -np.sum(p[p>0]*np.log(p[p>0]))
            print(f"{ts()} | INFO | [PPO {self.model_name}] rollout: actions={cnts.tolist()} p={np.round(p,3).tolist()} H={round2(float(ent))}")
        except Exception:
            pass

# -----------------------------------------------------------------------------
# Training single
# -----------------------------------------------------------------------------

@dataclass
class TrainConfig:
    symbol: str
    device: str
    timeframe: str
    mode: str
    version: str
    data_dir: str
    models_dir: str
    logs_dir: str

def train_single(cfg: TrainConfig) -> int:
    name = model_name(cfg.symbol, cfg.timeframe, cfg.mode, cfg.version)
    print(f"{ts()} | INFO | ================================================================================")
    print(f"{ts()} | INFO | [PPO] TRAINING MODEL: {name}")
    print(f"{ts()} | INFO | [PPO] CONFIG: symbol={cfg.symbol} timeframe={cfg.timeframe} mode={cfg.mode} version={cfg.version} device={cfg.device}")
    print(f"{ts()} | INFO | ================================================================================")

    ensure_dir(cfg.logs_dir)
    ensure_dir(cfg.models_dir)

    model_zip   = os.path.join(cfg.models_dir, f"{name}.zip")
    best_zip    = os.path.join(cfg.models_dir, f"{name}.best.zip")
    last_zip    = os.path.join(cfg.models_dir, f"{name}.last.zip")
    vecnorm_pkl = os.path.join(cfg.models_dir, f"{name}.vecnorm.pkl")
    csv_path    = os.path.join(cfg.logs_dir,   f"{name}.csv")

    train_env = make_env(cfg.symbol, cfg.timeframe, cfg.mode, cfg.data_dir, cfg.logs_dir)

    already = read_progress(cfg.logs_dir, name)

    if torch is not None and cfg.device == "cuda":
        try:
            cuda_ok = bool(torch.cuda.is_available())
            dev_name = torch.cuda.get_device_name(0) if cuda_ok else "N/A"
            print(f"{ts()} | INFO | [PPO {name}] torch device: cuda | cuda_available={cuda_ok}")
            print(f"{ts()} | INFO | [PPO {name}] cuda device name: {dev_name}")
        except Exception:
            pass

    # створення/завантаження моделі
    if os.path.exists(last_zip):
        try:
            model = PPO.load(last_zip, env=train_env, device=cfg.device, print_system_info=False)
        except Exception:
            model = PPO("MlpPolicy", train_env, verbose=0, device=cfg.device, n_steps=STEPS_PER_UPDATE)
    else:
        model = PPO("MlpPolicy", train_env, verbose=0, device=cfg.device, n_steps=STEPS_PER_UPDATE)

    write_spec(cfg.models_dir, name, {
        "algo": "PPO",
        "symbol": cfg.symbol,
        "timeframe": cfg.timeframe,
        "mode": cfg.mode,
        "version": cfg.version,
        "device": cfg.device,
        "total_updates": TOTAL_UPDATES,
        "steps_per_update": STEPS_PER_UPDATE,
        "total_timesteps": TOTAL_TIMESTEPS,
        "created_at": ts(),
    })

    print(f"{ts()} | INFO | [PPO {name}] total_plan={TOTAL_TIMESTEPS} | already_done={already} -> remaining={max(0,TOTAL_TIMESTEPS - already)}")

    # CSV header
    if not os.path.exists(csv_path):
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("update,steps,return_mean,return_std,best\n")

    eval_cb = CustomEvalCallback(train_env, eval_freq=STEPS_PER_UPDATE, n_episodes=5, verbose=0)
    diag_cb = RolloutDiagCallback(name)
    cb = CallbackList([eval_cb, diag_cb])

    best_ret = -1e9
    steps_done = already
    upd = 0

    while steps_done < TOTAL_TIMESTEPS:
        model.learn(
            total_timesteps=STEPS_PER_UPDATE,
            log_interval=None,
            callback=cb,
            progress_bar=False,
            reset_num_timesteps=False
        )
        steps_done += STEPS_PER_UPDATE
        upd += 1

        ret_mean = getattr(eval_cb, "last_mean_reward", 0.0)
        ret_std  = getattr(eval_cb, "last_std_reward", float("nan"))
        if ret_mean > best_ret:
            best_ret = float(ret_mean)
            model.save(best_zip)

        print(
            f"{ts()} | INFO | [PPO {name}] upd={upd:04d} steps={steps_done} "
            f"ret={round2(float(ret_mean))}±{('nan' if math.isnan(ret_std) else round2(float(ret_std)))} "
            f"best={round2(float(best_ret)) if best_ret > -1e8 else '—'}"
        )

        try:
            with open(csv_path, "a", encoding="utf-8") as f:
                f.write(f"{upd},{steps_done},{ret_mean},{ret_std},{best_ret}\n")
        except Exception:
            pass

        model.save(last_zip)
        train_env.save(vecnorm_pkl)
        write_progress(cfg.logs_dir, name, steps_done)

    model.save(model_zip)
    train_env.save(vecnorm_pkl)

    print(f"{ts()} | INFO | [PPO {name}] saved: {model_zip}")
    print(f"{ts()} | INFO | [PPO {name}] csv:   {csv_path}")
    print(f"{ts()} | INFO | [PPO {name}] best:  {best_zip}")
    print(f"{ts()} | INFO | [PPO {name}] vecnormalize: {vecnorm_pkl}")
    print(f"{ts()} | INFO | [PPO {name}] spec:  {os.path.join(cfg.models_dir, f'{name}.spec.json')}")

    return 0

# -----------------------------------------------------------------------------
# Parallel runner
# -----------------------------------------------------------------------------

class Worker(threading.Thread):
    def __init__(self, cfg: TrainConfig):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.rc: Optional[int] = None
        self.exc: Optional[str] = None

    def run(self):
        try:
            self.rc = train_single(self.cfg)
        except BaseException:
            self.rc = 1
            self.exc = traceback.format_exc()
            print(f"{ts()} | ERROR | [PPO PARALLEL] crash in {model_name(self.cfg.symbol, self.cfg.timeframe, self.cfg.mode, self.cfg.version)}: {self.exc}".rstrip())

@dataclass
class ParallelState:
    max_parallel: int = MAX_PARALLEL
    limit: int = 1                  # soft cap that grows only on scheduled checks
    next_check_at: float = 0.0      # when to run the next periodic check
    active: Dict[str, Worker] = None
    start_backoff_until: float = 0.0   # kept for compatibility (unused)
    last_start_log: float = 0.0        # kept for compatibility (unused)

    def __post_init__(self):
        if self.active is None:
            self.active = {}

# --- helpers for queue/build/skip ------------------------------------------------

def build_queue(symbol: str, device: str, data_dir: str, models_dir: str, logs_dir: str,
                timeframes: List[str], modes: List[str], versions: List[str]) -> List['TrainConfig']:
    q: List[TrainConfig] = []
    for tf in timeframes:
        for mode in modes:
            for ver in versions:
                q.append(TrainConfig(
                    symbol=symbol,
                    device=device,
                    timeframe=tf,
                    mode=mode,
                    version=ver,
                    data_dir=data_dir,
                    models_dir=models_dir,
                    logs_dir=logs_dir
                ))
    return q

def filter_already_trained(queue: List['TrainConfig']) -> Tuple[List['TrainConfig'], int]:
    kept: List[TrainConfig] = []
    skipped = 0
    for cfg in queue:
        name = model_name(cfg.symbol, cfg.timeframe, cfg.mode, cfg.version)
        done = read_progress(cfg.logs_dir, name)
        if done >= TOTAL_TIMESTEPS:
            skipped += 1
            continue
        kept.append(cfg)
    return kept, skipped

# --- utilization + device choice ------------------------------------------------

def get_gpu_cpu_ram_util() -> float:
    cpu = psutil.cpu_percent(interval=0.2)/100.0 if psutil else 0.0
    ram = psutil.virtual_memory().percent/100.0 if psutil else 0.0
    gpu = get_gpu_utilization()
    return max(cpu, ram, gpu)

def choose_device_for_job() -> Tuple[Optional[str], float, float]:
    """
    Returns ('cuda' or 'cpu', cpu_util, gpu_util) or (None, cpu, gpu) if both >= threshold.
    Priority: GPU if gpu < threshold; else CPU if cpu < threshold.
    """
    cpu, _ram = get_cpu_ram_utilization()
    gpu = get_gpu_utilization()
    if gpu < UTIL_THRESHOLD:
        return "cuda", cpu, gpu
    if cpu < UTIL_THRESHOLD:
        return "cpu", cpu, gpu
    return None, cpu, gpu

# --- periodic scheduler ---------------------------------------------------------

def should_check(now: float, st: ParallelState) -> bool:
    return now >= st.next_check_at

def schedule_after_ok(st: ParallelState):
    st.next_check_at = time.time() + CHECK_COOLDOWN_OK

def schedule_after_high(st: ParallelState):
    st.next_check_at = time.time() + CHECK_COOLDOWN_HIGH

def schedule_first_check(st: ParallelState):
    # first periodic check in 2 minutes (we still do ONE initial start attempt below)
    st.next_check_at = time.time() + CHECK_COOLDOWN_OK

def start_one_job(st: ParallelState, queue: List[TrainConfig]) -> bool:
    if len(st.active) >= min(st.limit, st.max_parallel):
        return False
    if not queue:
        return False

    device, cpu, gpu = choose_device_for_job()
    if device is None:
        # over the threshold, cannot start now
        return False

    cfg = queue.pop(0)
    cfg = TrainConfig(
        symbol=cfg.symbol,
        device=device,  # override per-job device choice
        timeframe=cfg.timeframe,
        mode=cfg.mode,
        version=cfg.version,
        data_dir=cfg.data_dir,
        models_dir=cfg.models_dir,
        logs_dir=cfg.logs_dir
    )
    name = model_name(cfg.symbol, cfg.timeframe, cfg.mode, cfg.version)
    print(f"{ts()} | INFO | [PPO PARALLEL] starting ({len(st.active)+1}/{st.limit}) -> {name} on {device} (cpu={round2(cpu)}, gpu={round2(gpu)}, norm={UTIL_THRESHOLD:.2f})")
    w = Worker(cfg)
    st.active[name] = w
    w.start()
    return True

def periodic_check_and_maybe_start(st: ParallelState, queue: List[TrainConfig]):
    """
    The ONLY recurring check:
      - if max load < threshold: schedule next in 2 min, increase limit by 1 (up to max) and start up to (limit - active) jobs
      - else: schedule next in 15 min, do not start anything
    """
    cpu, _ram = get_cpu_ram_utilization()
    gpu = get_gpu_utilization()
    max_util = max(cpu, gpu)

    print(f"{ts()} | INFO | [PPO PARALLEL] periodic check -> cpu={round2(cpu)} gpu={round2(gpu)} (thr={UTIL_THRESHOLD:.2f}) act={len(st.active)} lim={st.limit} max={st.max_parallel}")

    if max_util < UTIL_THRESHOLD:
        # schedule next quick check
        schedule_after_ok(st)
        # grow soft limit by 1 (bounded)
        if st.limit < st.max_parallel:
            st.limit += 1
        # start up to (limit - active) jobs
        to_start = max(0, min(st.limit, st.max_parallel) - len(st.active))
        for _ in range(to_start):
            if not start_one_job(st, queue):
                break
    else:
        # too busy -> slow cadence
        schedule_after_high(st)

def sweep_finished(st: ParallelState):
    finished = []
    for name, w in list(st.active.items()):
        if not w.is_alive():
            finished.append(name)
            if w.rc == 0:
                print(f"{ts()} | INFO | [PPO PARALLEL] finished OK -> {name}")
            else:
                print(f"{ts()} | INFO | [PPO PARALLEL] finished with ERR (rc={w.rc}) -> {name}")
    # remove finished from active
    for name in finished:
        st.active.pop(name, None)
    # close parallel slots: decrease limit by number of finished workers
    if finished:
        old_lim = st.limit
        st.limit = max(0, st.limit - len(finished))
        print(f"{ts()} | INFO | [PPO PARALLEL] slots closed: -{len(finished)} -> limit={st.limit} (was {old_lim}), active={len(st.active)}")

# -----------------------------------------------------------------------------
# CLI / main
# -----------------------------------------------------------------------------

@dataclass
class TrainConfig:
    symbol: str
    device: str
    timeframe: str
    mode: str
    version: str
    data_dir: str
    models_dir: str
    logs_dir: str

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--device", choices=["cpu","cuda"], default="cpu")
    ap.add_argument("--timeframes", default="1d", help="comma-separated, e.g. 1d or 1h,4h,1d")
    ap.add_argument("--modes", default="long,short", help="long,short or long")
    ap.add_argument("--versions", default="V1,V2,V3,V4,V5,V6,V7,V8,V9,V10,V11,V12,V13,V14,V15")
    ap.add_argument("--max-parallel", type=int, default=MAX_PARALLEL)
    args = ap.parse_args()

    symbol = args.symbol.upper()

    data_dir = os.path.join("test", "data", symbol)
    models_dir = os.path.join("models", symbol, "PPO")
    logs_dir   = os.path.join("logs",   symbol, "PPO")
    ensure_dir(models_dir)
    ensure_dir(logs_dir)

    print(f"{ts()} | INFO | [PPO] start training: symbol={symbol} device={args.device}")

    timeframes = [s.strip() for s in args.timeframes.split(",") if s.strip()]
    modes      = [s.strip() for s in args.modes.split(",") if s.strip()]
    versions   = [s.strip() for s in args.versions.split(",") if s.strip()]

    raw_queue = build_queue(symbol, args.device, data_dir, models_dir, logs_dir, timeframes, modes, versions)
    queue, skipped = filter_already_trained(raw_queue)
    if skipped > 0:
        print(f"{ts()} | INFO | [PPO] skip {skipped} already-trained config(s)")

    st = ParallelState(max_parallel=min(args.max_parallel, MAX_PARALLEL), limit=1)
    schedule_first_check(st)

    # --- Initial ONE-TIME attempt to start the first job (not a periodic check) ---
    if queue:
        device, cpu, gpu = choose_device_for_job()
        if device is not None:
            cfg0 = queue.pop(0)
            cfg0 = TrainConfig(
                symbol=cfg0.symbol,
                device=device,
                timeframe=cfg0.timeframe,
                mode=cfg0.mode,
                version=cfg0.version,
                data_dir=cfg0.data_dir,
                models_dir=cfg0.models_dir,
                logs_dir=cfg0.logs_dir
            )
            name0 = model_name(cfg0.symbol, cfg0.timeframe, cfg0.mode, cfg0.version)
            print(f"{ts()} | INFO | [PPO PARALLEL] starting ({len(st.active)+1}/{st.limit}) -> {name0} on {device} (cpu={round2(cpu)}, gpu={round2(gpu)}, norm={UTIL_THRESHOLD:.2f})")
            w0 = Worker(cfg0)
            st.active[name0] = w0
            w0.start()

    # --- Main loop: ONLY periodic scheduled checks + finish sweeping ---
    while queue or st.active:
        sweep_finished(st)
        now = time.time()
        if should_check(now, st):
            periodic_check_and_maybe_start(st, queue)
        time.sleep(0.3)

    print(f"{ts()} | INFO | [PPO] all jobs done.")

if __name__ == "__main__":
    try:
        main()
    finally:
        try:
            if _NVML_OK:
                pynvml.nvmlShutdown()
        except Exception:
            pass
