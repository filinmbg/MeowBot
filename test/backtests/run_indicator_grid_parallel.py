from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "ADAUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "BNBUSDT",
]

DEFAULT_TIMEFRAMES = ["15m", "30m", "1h", "4h", "1d"]


@dataclass(frozen=True)
class BacktestJob:
    job_name: str
    symbols: list[str]
    timeframes: list[str]


@dataclass
class RunningJob:
    job: BacktestJob
    run_id: str
    process: subprocess.Popen
    log_path: Path
    started_at: float
    log_file_handle: object = field(repr=False)


@dataclass
class FinishedJob:
    job_name: str
    run_id: str
    returncode: int
    log_path: Path
    started_at: float
    finished_at: float


def utc_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def sanitize_name(value: str) -> str:
    return (
        value.replace("/", "_")
        .replace("\\", "_")
        .replace(":", "_")
        .replace(" ", "_")
    )


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def build_jobs(
    symbols: list[str],
    timeframes: list[str],
    job_mode: str,
) -> list[BacktestJob]:
    jobs: list[BacktestJob] = []

    if job_mode == "symbol":
        for symbol in symbols:
            jobs.append(
                BacktestJob(
                    job_name=symbol,
                    symbols=[symbol],
                    timeframes=timeframes,
                )
            )

    elif job_mode == "symbol_timeframe":
        for symbol in symbols:
            for timeframe in timeframes:
                jobs.append(
                    BacktestJob(
                        job_name=f"{symbol}_{timeframe}",
                        symbols=[symbol],
                        timeframes=[timeframe],
                    )
                )

    else:
        raise ValueError(f"Unsupported job_mode: {job_mode}")

    return jobs


def build_command(
    *,
    job: BacktestJob,
    run_id: str,
    script_path: str,
    data_dir: str,
    results_dir: str,
    max_rules_per_side: int,
    max_trades_per_rule: int,
    only_side: str,
    edge_filter_profile: str,
    deposit: float,
    margin_pct: float,
    leverage: float,
    fee_rate: float,
    tp_pcts: list[float],
    tp_parts: list[float],
    sl_pct: float,
    sl_after_tp1_pct: float,
    sl_after_tp2_pct: float,
    sl_after_tp3_pct: float,
    max_hold_hours: float,
    intrabar_mode: str,
    make_edge_report: bool,
    min_trades_summary: int,
    min_trades_edge: int,
) -> list[str]:
    cmd = [
        sys.executable,
        "-u",
        script_path,
        "--data-dir",
        data_dir,
        "--results-dir",
        results_dir,
        "--run-id",
        run_id,
        "--symbols",
        *job.symbols,
        "--timeframes",
        *job.timeframes,
        "--only-side",
        only_side,
        "--edge-filter-profile",
        edge_filter_profile,
        "--max-rules-per-side",
        str(max_rules_per_side),
        "--max-trades-per-rule",
        str(max_trades_per_rule),
        "--min-trades-summary",
        str(min_trades_summary),
        "--deposit",
        str(deposit),
        "--margin-pct",
        str(margin_pct),
        "--leverage",
        str(leverage),
        "--fee-rate",
        str(fee_rate),
        "--tp-pcts",
        *[str(x) for x in tp_pcts],
        "--tp-parts",
        *[str(x) for x in tp_parts],
        "--sl-pct",
        str(sl_pct),
        "--sl-after-tp1-pct",
        str(sl_after_tp1_pct),
        "--sl-after-tp2-pct",
        str(sl_after_tp2_pct),
        "--sl-after-tp3-pct",
        str(sl_after_tp3_pct),
        "--max-hold-hours",
        str(max_hold_hours),
        "--intrabar-mode",
        intrabar_mode,
        "--min-trades-edge",
        str(min_trades_edge),
    ]

    if make_edge_report:
        cmd.append("--make-edge-report")

    return cmd


def read_last_relevant_log_line(log_path: Path, max_lines: int = 300) -> str:
    if not log_path.exists():
        return "log not created yet"

    try:
        with log_path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            file_size = f.tell()

            read_size = min(file_size, 80_000)

            if read_size <= 0:
                return "log is empty"

            f.seek(-read_size, os.SEEK_END)
            raw = f.read().decode("utf-8", errors="replace")

        lines = raw.splitlines()[-max_lines:]

        preferred_markers = [
            "[PROGRESS]",
            "[EDGE FILTER]",
            "[BT]",
            "[READ IND]",
            "[READ 1m]",
            "[EDGE]",
            "Backtest finished",
            "Trades:",
            "Summary file:",
            "Edge report:",
            "[ERROR]",
            "Traceback",
        ]

        for marker in preferred_markers:
            for line in reversed(lines):
                if marker in line:
                    return line.strip()

        for line in reversed(lines):
            line = line.strip()
            if line:
                return line

        return "log is empty"

    except Exception as e:
        return f"failed to read log: {e}"


def start_job(
    *,
    job: BacktestJob,
    base_run_id: str,
    args: argparse.Namespace,
    logs_dir: Path,
) -> RunningJob:
    safe_job_name = sanitize_name(job.job_name)
    run_id = f"{base_run_id}_{safe_job_name}"

    log_path = logs_dir / f"{safe_job_name}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = build_command(
        job=job,
        run_id=run_id,
        script_path=args.script_path,
        data_dir=args.data_dir,
        results_dir=args.results_dir,
        max_rules_per_side=args.max_rules_per_side,
        max_trades_per_rule=args.max_trades_per_rule,
        only_side=args.only_side,
        edge_filter_profile=args.edge_filter_profile,
        deposit=args.deposit,
        margin_pct=args.margin_pct,
        leverage=args.leverage,
        fee_rate=args.fee_rate,
        tp_pcts=args.tp_pcts,
        tp_parts=args.tp_parts,
        sl_pct=args.sl_pct,
        sl_after_tp1_pct=args.sl_after_tp1_pct,
        sl_after_tp2_pct=args.sl_after_tp2_pct,
        sl_after_tp3_pct=args.sl_after_tp3_pct,
        max_hold_hours=args.max_hold_hours,
        intrabar_mode=args.intrabar_mode,
        make_edge_report=args.make_edge_report,
        min_trades_summary=args.min_trades_summary,
        min_trades_edge=args.min_trades_edge,
    )

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"

    log_file = log_path.open("w", encoding="utf-8", newline="")

    log_file.write("=" * 120 + "\n")
    log_file.write(f"JOB: {job.job_name}\n")
    log_file.write(f"RUN_ID: {run_id}\n")
    log_file.write("CMD:\n")
    log_file.write(" ".join(cmd) + "\n")
    log_file.write("=" * 120 + "\n")
    log_file.flush()

    process = subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )

    return RunningJob(
        job=job,
        run_id=run_id,
        process=process,
        log_path=log_path,
        started_at=time.time(),
        log_file_handle=log_file,
    )


def print_dashboard(
    *,
    base_run_id: str,
    total_jobs: int,
    waiting_jobs: list[BacktestJob],
    running_jobs: list[RunningJob],
    finished_jobs: list[FinishedJob],
    failed_jobs: list[FinishedJob],
    logs_dir: Path,
) -> None:
    now = time.time()

    done_count = len(finished_jobs)
    failed_count = len(failed_jobs)
    running_count = len(running_jobs)
    waiting_count = len(waiting_jobs)

    print("\n" + "=" * 140)
    print("MeowBot parallel progress")
    print(f"Base run id: {base_run_id}")
    print(
        f"DONE: {done_count}/{total_jobs} | "
        f"RUNNING: {running_count} | "
        f"WAITING: {waiting_count} | "
        f"FAILED: {failed_count}"
    )
    print(f"Logs dir: {logs_dir}")
    print("-" * 140)

    if running_jobs:
        print("RUNNING JOBS:")
        for item in running_jobs:
            elapsed = format_duration(now - item.started_at)
            pid = item.process.pid
            last_line = read_last_relevant_log_line(item.log_path)

            print(
                f"[RUNNING] {item.job.job_name:<16} "
                f"pid={pid:<8} "
                f"elapsed={elapsed:<10} "
                f"{last_line}"
            )

    if finished_jobs:
        print("-" * 140)
        print("LAST FINISHED:")
        for item in finished_jobs[-8:]:
            elapsed = format_duration(item.finished_at - item.started_at)
            status = "OK" if item.returncode == 0 else "ERROR"

            print(
                f"[{status:<5}] {item.job_name:<16} "
                f"elapsed={elapsed:<10} "
                f"returncode={item.returncode:<4} "
                f"log={item.log_path}"
            )

    if failed_jobs:
        print("-" * 140)
        print("FAILED JOBS:")
        for item in failed_jobs:
            elapsed = format_duration(item.finished_at - item.started_at)
            print(
                f"[FAILED] {item.job_name:<16} "
                f"elapsed={elapsed:<10} "
                f"returncode={item.returncode:<4} "
                f"log={item.log_path}"
            )

    print("=" * 140)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Parallel launcher for MeowBot indicator rule grid backtests with live progress."
    )

    parser.add_argument(
        "--script-path",
        type=str,
        default="test/backtests/backtest_indicator_rule_grid.py",
    )

    parser.add_argument(
        "--data-dir",
        type=str,
        default="test/data",
    )

    parser.add_argument(
        "--results-dir",
        type=str,
        default="test/results/indicator_rule_grid",
    )

    parser.add_argument(
        "--base-run-id",
        type=str,
        default=None,
    )

    parser.add_argument(
        "--symbols",
        nargs="+",
        default=DEFAULT_SYMBOLS,
    )

    parser.add_argument(
        "--timeframes",
        nargs="+",
        default=DEFAULT_TIMEFRAMES,
    )

    parser.add_argument(
        "--job-mode",
        choices=["symbol", "symbol_timeframe"],
        default="symbol",
        help="symbol = one process per symbol. symbol_timeframe = one process per symbol+timeframe.",
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=3,
        help="Parallel processes. Recommended: 2-4.",
    )

    parser.add_argument(
        "--progress-seconds",
        type=float,
        default=30.0,
        help="How often to print dashboard.",
    )

    parser.add_argument(
        "--only-side",
        choices=["both", "long", "short"],
        default="both",
    )

    parser.add_argument(
        "--edge-filter-profile",
        choices=["none", "v1", "v1_taker"],
        default="none",
        help="Additional edge filters passed to backtest jobs.",
    )

    parser.add_argument(
        "--max-rules-per-side",
        type=int,
        default=0,
        help="0 = all rules.",
    )

    parser.add_argument(
        "--max-trades-per-rule",
        type=int,
        default=0,
        help="0 = no limit.",
    )

    parser.add_argument(
        "--min-trades-summary",
        type=int,
        default=20,
    )

    parser.add_argument("--deposit", type=float, default=100.0)
    parser.add_argument("--margin-pct", type=float, default=1.0)
    parser.add_argument("--leverage", type=float, default=20.0)
    parser.add_argument("--fee-rate", type=float, default=0.0005)

    parser.add_argument(
        "--tp-pcts",
        nargs="+",
        type=float,
        default=[1.0, 2.0, 3.0, 4.0],
    )

    parser.add_argument(
        "--tp-parts",
        nargs="+",
        type=float,
        default=[0.25, 0.25, 0.25, 0.25],
    )

    parser.add_argument("--sl-pct", type=float, default=2.0)
    parser.add_argument("--sl-after-tp1-pct", type=float, default=0.0)
    parser.add_argument("--sl-after-tp2-pct", type=float, default=0.5)
    parser.add_argument("--sl-after-tp3-pct", type=float, default=1.0)
    parser.add_argument("--max-hold-hours", type=float, default=168.0)

    parser.add_argument(
        "--intrabar-mode",
        choices=["conservative", "tp_first"],
        default="conservative",
    )

    parser.add_argument(
        "--make-edge-report",
        action="store_true",
        help="Generate edge_report inside every parallel job.",
    )

    parser.add_argument(
        "--min-trades-edge",
        type=int,
        default=30,
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    base_run_id = args.base_run_id or f"parallel_{utc_run_id()}"
    results_dir = Path(args.results_dir)
    logs_dir = results_dir / base_run_id / "_logs"

    symbols = [s.upper() for s in args.symbols]
    timeframes = args.timeframes

    jobs = build_jobs(
        symbols=symbols,
        timeframes=timeframes,
        job_mode=args.job_mode,
    )

    waiting_jobs = list(jobs)
    running_jobs: list[RunningJob] = []
    finished_jobs: list[FinishedJob] = []
    failed_jobs: list[FinishedJob] = []

    total_jobs = len(jobs)

    print("=" * 120)
    print("MeowBot parallel indicator grid launcher")
    print(f"Base run id:          {base_run_id}")
    print(f"Job mode:             {args.job_mode}")
    print(f"Workers:              {args.workers}")
    print(f"Jobs:                 {total_jobs}")
    print(f"Symbols:              {symbols}")
    print(f"Timeframes:           {timeframes}")
    print(f"Max rules per side:   {args.max_rules_per_side}")
    print(f"Max trades per rule:  {args.max_trades_per_rule}")
    print(f"Edge filter profile:  {args.edge_filter_profile}")
    print(f"TP pcts:              {args.tp_pcts}")
    print(f"SL pct:               {args.sl_pct}")
    print(f"Progress every:       {args.progress_seconds}s")
    print(f"Logs dir:             {logs_dir}")
    print("=" * 120)

    for job in jobs:
        print(f"[JOB] {job.job_name}: symbols={job.symbols}, timeframes={job.timeframes}")

    print("=" * 120)

    last_dashboard_at = 0.0

    try:
        while waiting_jobs or running_jobs:
            while waiting_jobs and len(running_jobs) < args.workers:
                job = waiting_jobs.pop(0)

                running = start_job(
                    job=job,
                    base_run_id=base_run_id,
                    args=args,
                    logs_dir=logs_dir,
                )

                running_jobs.append(running)

                print(
                    f"[START] {job.job_name} "
                    f"pid={running.process.pid} "
                    f"run_id={running.run_id} "
                    f"log={running.log_path}"
                )

            still_running: list[RunningJob] = []

            for item in running_jobs:
                returncode = item.process.poll()

                if returncode is None:
                    still_running.append(item)
                    continue

                try:
                    item.log_file_handle.flush()
                    item.log_file_handle.close()
                except Exception:
                    pass

                finished = FinishedJob(
                    job_name=item.job.job_name,
                    run_id=item.run_id,
                    returncode=returncode,
                    log_path=item.log_path,
                    started_at=item.started_at,
                    finished_at=time.time(),
                )

                finished_jobs.append(finished)

                status = "OK" if returncode == 0 else "ERROR"

                if returncode != 0:
                    failed_jobs.append(finished)

                print(
                    f"[{status}] {finished.job_name} "
                    f"returncode={returncode} "
                    f"elapsed={format_duration(finished.finished_at - finished.started_at)} "
                    f"log={finished.log_path}"
                )

            running_jobs = still_running

            now = time.time()

            if now - last_dashboard_at >= args.progress_seconds:
                print_dashboard(
                    base_run_id=base_run_id,
                    total_jobs=total_jobs,
                    waiting_jobs=waiting_jobs,
                    running_jobs=running_jobs,
                    finished_jobs=finished_jobs,
                    failed_jobs=failed_jobs,
                    logs_dir=logs_dir,
                )
                last_dashboard_at = now

            time.sleep(2.0)

    except KeyboardInterrupt:
        print("\n[INTERRUPT] Ctrl+C received. Terminating running jobs...")

        for item in running_jobs:
            try:
                print(f"[TERMINATE] {item.job.job_name} pid={item.process.pid}")
                item.process.terminate()
            except Exception:
                pass

        time.sleep(3.0)

        for item in running_jobs:
            try:
                if item.process.poll() is None:
                    print(f"[KILL] {item.job.job_name} pid={item.process.pid}")
                    item.process.kill()
            except Exception:
                pass

        for item in running_jobs:
            try:
                item.log_file_handle.flush()
                item.log_file_handle.close()
            except Exception:
                pass

        raise

    print_dashboard(
        base_run_id=base_run_id,
        total_jobs=total_jobs,
        waiting_jobs=waiting_jobs,
        running_jobs=running_jobs,
        finished_jobs=finished_jobs,
        failed_jobs=failed_jobs,
        logs_dir=logs_dir,
    )

    print("=" * 120)
    print("Parallel run finished")
    print(f"Base run id: {base_run_id}")
    print(f"Finished:    {len(finished_jobs)}")
    print(f"Failed:      {len(failed_jobs)}")

    if failed_jobs:
        print("Failed jobs:")
        for item in failed_jobs:
            print(f"- {item.job_name} | log={item.log_path}")

    print("=" * 120)


if __name__ == "__main__":
    main()