#!/usr/bin/env python3
"""Plot substation/source voltage traces for all feeder logs in one run."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd

from plot_distribution_from_logs import parse_distribution_log


FEEDER_LOG_RE = re.compile(r"feeder_(?P<index>\d+)\.log$")


def feeder_index_from_log(log_path: Path) -> int | None:
    match = FEEDER_LOG_RE.search(log_path.name)
    return int(match.group("index")) if match else None


def feeder_sort_key(log_path: Path) -> tuple[int, str]:
    index = feeder_index_from_log(log_path)
    return (index if index is not None else 10**9, log_path.name)


def feeder_label(log_path: Path, parsed: pd.DataFrame) -> str:
    if "feeder" in parsed.columns and parsed["feeder"].notna().any():
        return f"Feeder {int(parsed['feeder'].dropna().iloc[-1])}"
    index = feeder_index_from_log(log_path)
    return f"Feeder {index}" if index is not None else log_path.stem


def select_settled_source_series(parsed: pd.DataFrame) -> pd.DataFrame:
    """Return one source-voltage row per granted time."""
    by_t = (
        parsed.assign(
            update_rank=parsed["vupdate"].astype(int),
            state_rank=(parsed["state"] == "NEXT_STEP").astype(int),
        )
        .sort_values(["t_granted", "update_rank", "state_rank", "iter"])
        .groupby("t_granted", as_index=False)
        .last()
        .sort_values("t_granted")
        .drop(columns=["update_rank", "state_rank"])
    )
    columns = [
        "t_granted",
        "source_v_pu",
        "source_ang_deg",
        "vupdate",
        "state",
    ]
    return by_t[[column for column in columns if column in by_t.columns]].copy()


def load_feeder_substation_series(log_path: Path) -> pd.DataFrame:
    parsed = parse_distribution_log(log_path)
    series = select_settled_source_series(parsed)
    if series.empty or "source_v_pu" not in series.columns:
        raise RuntimeError(f"No source voltage rows found in {log_path}")
    series.insert(0, "feeder", feeder_label(log_path, parsed))
    series.insert(1, "feeder_index", feeder_index_from_log(log_path))
    series.insert(2, "feeder_log", str(log_path))
    return series


def find_feeder_logs(run_dir: Path) -> list[Path]:
    return sorted(run_dir.glob("feeder_*.log"), key=feeder_sort_key)


def load_all_feeder_substation_series(
    run_dir: Path | None,
    logs: list[Path],
) -> pd.DataFrame:
    feeder_logs = list(logs)
    if run_dir is not None:
        feeder_logs.extend(find_feeder_logs(run_dir))

    seen = set()
    unique_logs = []
    for log_path in feeder_logs:
        resolved = log_path.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_logs.append(resolved)

    if not unique_logs:
        location = str(run_dir) if run_dir is not None else "the requested inputs"
        raise FileNotFoundError(f"No feeder_*.log files found in {location}")

    frames = []
    for log_path in sorted(unique_logs, key=feeder_sort_key):
        try:
            frames.append(load_feeder_substation_series(log_path))
        except RuntimeError as exc:
            print(f"[WARN] Skipping {log_path}: {exc}")

    if not frames:
        raise RuntimeError("No feeder substation voltage data could be parsed.")
    return pd.concat(frames, ignore_index=True)


def time_axis_seconds_or_hours(t_seconds: pd.Series):
    if t_seconds.nunique() >= 2 and (t_seconds.max() - t_seconds.min()) >= 3600:
        return t_seconds / 3600.0, "Time (hours)"
    return t_seconds, "Time (s)"


def plot_feeder_substation_voltages(
    data: pd.DataFrame,
    out_dir: Path,
    x_limits: tuple[float, float] | None = None,
    voltage_y_limits: tuple[float, float] | None = None,
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "feeder_substation_voltages.csv"
    plot_path = out_dir / "feeder_substation_voltages.png"
    data.to_csv(csv_path, index=False)

    fig, ax = plt.subplots(figsize=(9.5, 5.5))
    for feeder, group in data.sort_values(["feeder_index", "t_granted"]).groupby(
        "feeder", sort=False
    ):
        x, xlabel = time_axis_seconds_or_hours(group["t_granted"])
        ax.plot(x, group["source_v_pu"], linewidth=1.8, label=feeder)

    ax.set_xlabel(xlabel)
    ax.set_ylabel("Distribution feeder source/substation |V| (pu)")
    ax.grid(True)
    ax.legend(title="Feeder")
    if x_limits is not None:
        ax.set_xlim(*x_limits)
    if voltage_y_limits is not None:
        ax.set_ylim(*voltage_y_limits)
    fig.tight_layout()
    fig.savefig(plot_path, dpi=300)
    plt.close(fig)
    return csv_path, plot_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot source/substation voltage from every feeder_*.log in one run."
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=Path("."),
        help="Run folder containing feeder_*.log files.",
    )
    parser.add_argument(
        "--log",
        action="append",
        type=Path,
        default=[],
        help="Specific feeder log to include. May be supplied multiple times.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output folder. Defaults to RUN_DIR/plots.",
    )
    parser.add_argument(
        "--xlim",
        type=float,
        nargs=2,
        metavar=("XMIN", "XMAX"),
        default=None,
        help="Optional x-axis limits in seconds.",
    )
    parser.add_argument(
        "--voltage-ylim",
        type=float,
        nargs=2,
        metavar=("YMIN", "YMAX"),
        default=None,
        help="Optional y-axis limits in pu.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_dir = args.run_dir.expanduser().resolve() if args.run_dir is not None else None
    logs = [log.expanduser().resolve() for log in args.log]
    out_dir = args.out.expanduser().resolve() if args.out else run_dir / "plots"

    print(f"[INFO] Run dir: {run_dir}")
    print(f"[INFO] Out: {out_dir}")
    data = load_all_feeder_substation_series(run_dir, logs)
    csv_path, plot_path = plot_feeder_substation_voltages(
        data,
        out_dir,
        x_limits=tuple(args.xlim) if args.xlim is not None else None,
        voltage_y_limits=tuple(args.voltage_ylim)
        if args.voltage_ylim is not None
        else None,
    )
    print(f"[OK] Saved CSV: {csv_path}")
    print(f"[OK] Saved plot: {plot_path}")


if __name__ == "__main__":
    main()
