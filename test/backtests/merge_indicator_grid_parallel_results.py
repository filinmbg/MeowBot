from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def find_run_dirs(results_dir: Path, base_run_id: str) -> list[Path]:
    if not results_dir.exists():
        return []

    run_dirs = []

    for path in results_dir.iterdir():
        if not path.is_dir():
            continue

        if path.name == base_run_id:
            continue

        if path.name.startswith(base_run_id + "_"):
            run_dirs.append(path)

    return sorted(run_dirs)


def read_csv_if_exists(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None

    try:
        return pd.read_csv(path)
    except Exception as e:
        print(f"[ERROR] Failed to read {path}: {e}")
        return None


def merge_files(
    *,
    run_dirs: list[Path],
    filename: str,
    output_path: Path,
    sort_columns: list[str] | None = None,
    ascending: list[bool] | None = None,
) -> None:
    frames = []

    for run_dir in run_dirs:
        path = run_dir / filename

        df = read_csv_if_exists(path)

        if df is None or df.empty:
            continue

        df.insert(0, "source_run_id", run_dir.name)
        frames.append(df)

    if not frames:
        print(f"[SKIP] No files found for {filename}")
        return

    merged = pd.concat(frames, ignore_index=True)

    if sort_columns:
        existing_sort_columns = [col for col in sort_columns if col in merged.columns]

        if existing_sort_columns:
            if ascending and len(ascending) == len(sort_columns):
                existing_ascending = [
                    asc
                    for col, asc in zip(sort_columns, ascending)
                    if col in merged.columns
                ]
            else:
                existing_ascending = True

            merged = merged.sort_values(
                existing_sort_columns,
                ascending=existing_ascending,
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(output_path, index=False)

    print(f"[OK] Merged {filename}: rows={len(merged):,} -> {output_path}")


def make_top_files(output_dir: Path) -> None:
    summary_path = output_dir / "summary_all.csv"

    if summary_path.exists():
        summary = pd.read_csv(summary_path)

        if not summary.empty:
            sort_cols = []
            ascending = []

            for col, asc in [
                ("profit_factor", False),
                ("total_pnl_usd", False),
                ("winrate", False),
                ("trades", False),
            ]:
                if col in summary.columns:
                    sort_cols.append(col)
                    ascending.append(asc)

            if sort_cols:
                top = summary.sort_values(sort_cols, ascending=ascending)

                top_100 = top.head(100)
                top_100_path = output_dir / "top_100_rules.csv"
                top_100.to_csv(top_100_path, index=False)
                print(f"[OK] Saved top rules: {top_100_path}")

                if "trades" in top.columns:
                    top_liquid = top[top["trades"] >= 100].head(100)
                    top_liquid_path = output_dir / "top_100_rules_min_100_trades.csv"
                    top_liquid.to_csv(top_liquid_path, index=False)
                    print(f"[OK] Saved top liquid rules: {top_liquid_path}")

    edge_path = output_dir / "edge_all.csv"

    if edge_path.exists():
        edge = pd.read_csv(edge_path)

        if not edge.empty:
            bad_sort_cols = []
            bad_ascending = []

            for col, asc in [
                ("total_pnl_usd", True),
                ("profit_factor", True),
                ("winrate", True),
                ("trades", False),
            ]:
                if col in edge.columns:
                    bad_sort_cols.append(col)
                    bad_ascending.append(asc)

            if bad_sort_cols:
                bad = edge.sort_values(bad_sort_cols, ascending=bad_ascending)
                bad_path = output_dir / "worst_edges.csv"
                bad.head(300).to_csv(bad_path, index=False)
                print(f"[OK] Saved worst edges: {bad_path}")

            good_sort_cols = []
            good_ascending = []

            for col, asc in [
                ("profit_factor", False),
                ("total_pnl_usd", False),
                ("winrate", False),
                ("trades", False),
            ]:
                if col in edge.columns:
                    good_sort_cols.append(col)
                    good_ascending.append(asc)

            if good_sort_cols:
                good = edge.sort_values(good_sort_cols, ascending=good_ascending)
                good_path = output_dir / "best_edges.csv"
                good.head(300).to_csv(good_path, index=False)
                print(f"[OK] Saved best edges: {good_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge parallel MeowBot indicator grid result summaries."
    )

    parser.add_argument(
        "--results-dir",
        type=str,
        default="test/results/indicator_rule_grid",
    )

    parser.add_argument(
        "--base-run-id",
        type=str,
        required=True,
        help="Example: parallel_20260426_090000",
    )

    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    results_dir = Path(args.results_dir)

    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = results_dir / f"{args.base_run_id}_MERGED"

    run_dirs = find_run_dirs(results_dir, args.base_run_id)

    print("=" * 100)
    print("Merge MeowBot parallel indicator grid results")
    print(f"Results dir:  {results_dir}")
    print(f"Base run id:  {args.base_run_id}")
    print(f"Run dirs:     {len(run_dirs)}")
    print(f"Output dir:   {output_dir}")
    print("=" * 100)

    for run_dir in run_dirs:
        print(f"[RUN] {run_dir.name}")

    print("=" * 100)

    if not run_dirs:
        raise RuntimeError(f"No run dirs found for base_run_id={args.base_run_id}")

    merge_files(
        run_dirs=run_dirs,
        filename="summary_by_rule.csv",
        output_path=output_dir / "summary_all.csv",
        sort_columns=["profit_factor", "total_pnl_usd", "winrate", "trades"],
        ascending=[False, False, False, False],
    )

    merge_files(
        run_dirs=run_dirs,
        filename="edge_report.csv",
        output_path=output_dir / "edge_all.csv",
        sort_columns=["total_pnl_usd", "profit_factor", "winrate", "trades"],
        ascending=[True, True, True, False],
    )

    merge_files(
        run_dirs=run_dirs,
        filename="rules_catalog.csv",
        output_path=output_dir / "rules_catalog_all.csv",
    )

    make_top_files(output_dir)

    print("=" * 100)
    print("Merge finished")
    print(f"Output dir: {output_dir}")
    print("=" * 100)


if __name__ == "__main__":
    main()