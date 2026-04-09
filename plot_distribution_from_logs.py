#!/usr/bin/env python3
"""
Parse feeder logs and plot the distribution-side bus voltage tracked by Distribution.py.

Usage:
  python3 plot_distribution_from_logs.py --log feeder_1.log
  python3 plot_distribution_from_logs.py --log feeder_1.log --out plots/
"""

import argparse
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd


HEADER_RE = re.compile(
    r"\[Feeder(?P<feeder>\d+)\]\s+iter=(?P<iter>\d+)\s+"
    r"t_granted=(?P<t_granted>[0-9.+\-eE]+)s.*state=(?P<state>[A-Z_]+)"
)
SOURCE_RE = re.compile(
    r"Vupdate=(?P<vupdate>True|False)\s+V=(?P<source_v>[0-9.+\-eE]+)\s+pu\s+"
    r"ang=(?P<source_ang_deg>[\-0-9.+eE]+)\s+deg"
)
DIST_RE = re.compile(
    r"DistBus=(?P<dist_bus>\S+)\s+Vavg=(?P<vavg>[0-9.+\-eE]+)\s+pu\s+"
    r"Va=(?P<va>[0-9.+\-eE]+|nan)\s+pu\s+"
    r"Vb=(?P<vb>[0-9.+\-eE]+|nan)\s+pu\s+"
    r"Vc=(?P<vc>[0-9.+\-eE]+|nan)\s+pu"
)


def _float_or_nan(value: str) -> float:
    return float("nan") if value.lower() == "nan" else float(value)


def _time_axis_seconds_or_hours(t_seconds: pd.Series):
    if t_seconds.nunique() >= 2 and (t_seconds.max() - t_seconds.min()) >= 3600:
        return t_seconds / 3600.0, "Time (hours)"
    return t_seconds, "Time (s)"


def _set_voltage_limits(ax, series_list):
    values = pd.concat(series_list, axis=0).dropna()
    if values.empty:
        return

    vmin = values.min()
    vmax = values.max()
    pad = max(0.002, 0.1 * max(vmax - vmin, 0.01))
    ax.set_ylim(vmin - pad, vmax + pad)


def parse_distribution_log(log_path: Path) -> pd.DataFrame:
    rows = []
    saw_dist_bus = False

    for line in log_path.read_text(errors="ignore").splitlines():
        header = HEADER_RE.search(line)
        if not header:
            continue

        row = {
            "feeder": int(header.group("feeder")),
            "iter": int(header.group("iter")),
            "t_granted": float(header.group("t_granted")),
            "state": header.group("state"),
        }

        source = SOURCE_RE.search(line)
        if source:
            row.update(
                {
                    "vupdate": source.group("vupdate") == "True",
                    "source_v_pu": float(source.group("source_v")),
                    "source_ang_deg": float(source.group("source_ang_deg")),
                }
            )

        dist = DIST_RE.search(line)
        if dist:
            saw_dist_bus = True
            row.update(
                {
                    "dist_bus": dist.group("dist_bus"),
                    "vavg_pu": float(dist.group("vavg")),
                    "va_pu": _float_or_nan(dist.group("va")),
                    "vb_pu": _float_or_nan(dist.group("vb")),
                    "vc_pu": _float_or_nan(dist.group("vc")),
                }
            )

        rows.append(row)

    if not rows:
        raise RuntimeError(
            "Parsed 0 feeder rows. Check that --log points to a feeder_*.log file."
        )

    df = pd.DataFrame(rows).sort_values(["t_granted", "iter"]).reset_index(drop=True)

    if not saw_dist_bus:
        raise RuntimeError(
            "No distribution-side bus voltage entries were found in the feeder log. "
            "Rerun the co-simulation with the updated Distribution.py so the feeder "
            "log includes the tracked OpenDSS bus voltage."
        )

    return df


def make_plots(df: pd.DataFrame, out_dir: Path, log_stem: str):
    out_dir.mkdir(parents=True, exist_ok=True)

    # Prefer the settled NEXT_STEP row at each granted time. If a time has no
    # NEXT_STEP row yet (for example the initial t=0 iterative convergence),
    # fall back to the latest row for that granted time.
    by_t = (
        df.assign(state_rank=(df["state"] == "NEXT_STEP").astype(int))
        .sort_values(["t_granted", "state_rank", "iter"])
        .groupby("t_granted", as_index=False)
        .last()
        .sort_values("t_granted")
        .drop(columns=["state_rank"])
    )
    x, xlabel = _time_axis_seconds_or_hours(by_t["t_granted"])
    dist_bus = by_t["dist_bus"].dropna().iloc[-1]
    feeder = int(by_t["feeder"].iloc[-1])

    fig, axes = plt.subplots(2, 1, sharex=True, figsize=(9, 7))

    axes[0].plot(x, by_t["source_v_pu"], label=f"Bus 2 |V|", linewidth=1.8)
    axes[0].set_ylabel("Voltage (pu)")
    axes[0].set_title(f"Feeder {feeder}: Interface vs distribution-side voltage")
    axes[0].grid(True)
    axes[0].set_ylim(0.90, 1.00)
    axes[0].legend()

    axes[1].plot(x, by_t["va_pu"], label="Phase A", linewidth=1.6)
    axes[1].plot(x, by_t["vb_pu"], label="Phase B", linewidth=1.6)
    axes[1].plot(x, by_t["vc_pu"], label="Phase C", linewidth=1.6)
    axes[1].plot(x, by_t["vavg_pu"], label="Average", linestyle="--", linewidth=1.8)
    axes[1].set_ylabel("Voltage (pu)")
    axes[1].set_xlabel(xlabel)
    axes[1].set_title(f"Distribution bus {dist_bus} voltage by phase")
    axes[1].set_ylim(0.92, 0.94)
    axes[1].grid(True)
    axes[1].legend()


    fig.suptitle("OpenDSS Distribution-Side Voltage vs Time", fontsize=14)
    plt.tight_layout()

    plot_path = out_dir / f"{log_stem}_distribution_voltage_vs_time.png"
    fig.savefig(plot_path, dpi=300)
    plt.close(fig)

    # Save a transmission-style single-line voltage plot for the tracked bus.
    fig, ax = plt.subplots(figsize=(8.5, 4.5))
    ax.plot(x, by_t["vavg_pu"], color="tab:green", linewidth=2, label=f"{dist_bus} |V|")
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Voltage magnitude |V| (pu)")
    ax.set_title(f"Distribution bus {dist_bus} voltage")
    ax.grid(True)
    ax.legend()
    _set_voltage_limits(ax, [by_t["vavg_pu"]])

    single_plot_path = out_dir / f"{log_stem}_distribution_bus_voltage_vs_time.png"
    fig.tight_layout()
    fig.savefig(single_plot_path, dpi=300)
    plt.close(fig)

    csv_path = out_dir / f"{log_stem}_distribution_voltage.csv"
    by_t.to_csv(csv_path, index=False)

    print(f"[OK] Saved CSV: {csv_path}")
    print(f"[OK] Saved plot: {plot_path}")
    print(f"[OK] Saved plot: {single_plot_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=str, default="feeder_1.log", help="Path to feeder log")
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Output folder (default: same folder as the log)",
    )
    args = parser.parse_args()

    log_path = Path(args.log).expanduser().resolve()
    if not log_path.exists():
        raise FileNotFoundError(f"Log not found: {log_path}")

    out_dir = Path(args.out).expanduser().resolve() if args.out else log_path.parent

    print(f"[INFO] Log: {log_path}")
    print(f"[INFO] Out: {out_dir}")

    df = parse_distribution_log(log_path)
    make_plots(df, out_dir, log_path.stem)


if __name__ == "__main__":
    main()
