#!/usr/bin/env python3
"""
Plot summary results from run_transmission_fault_scenarios.py.

This script reads the scenario summary CSV and produces comparison plots for:
  - survival / final simulation time
  - final Bus 2 voltage
  - generator stress metrics (delta spread, omega deviation, vf max)
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


DEFAULT_RESULTS = (
    Path(__file__).with_name("transmission_scenario_runs")
    / "transmission_scenario_results.csv"
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Plot standalone transmission scenario screening results."
    )
    parser.add_argument(
        "--results",
        type=Path,
        default=DEFAULT_RESULTS,
        help=(
            "Scenario summary CSV produced by run_transmission_fault_scenarios.py "
            f"(default: {DEFAULT_RESULTS})"
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "Output directory for figures. Defaults to the results CSV directory."
        ),
    )
    return parser.parse_args()


def load_results(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise RuntimeError(
            f"Scenario results CSV not found: {path}. "
            "Run run_transmission_fault_scenarios.py first."
        )
    df = pd.read_csv(path)
    required = {
        "name",
        "kind",
        "stable_to_target",
        "final_t",
        "target_time",
        "final_bus2_vmag",
        "delta_spread_deg",
        "omega_max_dev",
        "vf_max_pu",
    }
    missing = sorted(required - set(df.columns))
    if missing:
        raise RuntimeError(
            f"Scenario results CSV is missing required columns: {', '.join(missing)}"
        )

    if df.empty:
        raise RuntimeError("Scenario results CSV contains no rows to plot.")

    df["stable_to_target"] = df["stable_to_target"].astype(bool)
    df["kind"] = df["kind"].astype(str)
    df["name"] = df["name"].astype(str)
    df["label"] = df["name"] + " [" + df["kind"] + "]"
    df = df.sort_values(
        ["stable_to_target", "final_t", "name"],
        ascending=[False, False, True],
    ).reset_index(drop=True)
    return df


def scenario_colors(df: pd.DataFrame):
    return np.where(df["stable_to_target"], "#2e8b57", "#c44e52")


def save_survival_plot(df: pd.DataFrame, out_dir: Path):
    colors = scenario_colors(df)
    target_time = float(df["target_time"].max())
    y = np.arange(len(df))

    fig, ax = plt.subplots(figsize=(12, max(5, 0.55 * len(df) + 2)))
    ax.barh(y, df["final_t"], color=colors, alpha=0.9)
    ax.axvline(target_time, color="black", linestyle="--", linewidth=1.2, label="target time")
    ax.set_yticks(y)
    ax.set_yticklabels(df["label"])
    ax.invert_yaxis()
    ax.set_xlabel("Final simulated time (s)")
    ax.set_title("Transmission Scenario Survival Time")
    ax.grid(True, axis="x", alpha=0.3)
    ax.legend(loc="lower right")

    for yi, final_t in enumerate(df["final_t"]):
        ax.text(final_t, yi, f" {final_t:.3f}s", va="center", ha="left", fontsize=9)

    fig.tight_layout()
    fig.savefig(out_dir / "transmission_scenario_survival_time.png", dpi=150)
    plt.close(fig)


def save_bus2_plot(df: pd.DataFrame, out_dir: Path):
    colors = scenario_colors(df)
    y = np.arange(len(df))

    fig, ax = plt.subplots(figsize=(12, max(5, 0.55 * len(df) + 2)))
    ax.barh(y, df["final_bus2_vmag"], color=colors, alpha=0.9)
    ax.set_yticks(y)
    ax.set_yticklabels(df["label"])
    ax.invert_yaxis()
    ax.set_xlabel("Final Bus 2 |V| (pu)")
    ax.set_title("Final Transmission Bus 2 Voltage by Scenario")
    ax.grid(True, axis="x", alpha=0.3)

    vmin = float(np.nanmin(df["final_bus2_vmag"]))
    vmax = float(np.nanmax(df["final_bus2_vmag"]))
    span = max(vmax - vmin, 0.02)
    ax.set_xlim(max(0.0, vmin - 0.15 * span), min(1.2, vmax + 0.20 * span))

    for yi, value in enumerate(df["final_bus2_vmag"]):
        ax.text(value, yi, f" {value:.4f}", va="center", ha="left", fontsize=9)

    fig.tight_layout()
    fig.savefig(out_dir / "transmission_scenario_final_bus2_voltage.png", dpi=150)
    plt.close(fig)


def save_stress_plot(df: pd.DataFrame, out_dir: Path):
    colors = scenario_colors(df)
    x = np.arange(len(df))

    metrics = [
        ("delta_spread_deg", "Rotor-angle spread (deg)"),
        ("omega_max_dev", "Max |omega - 1| (pu)"),
        ("vf_max_pu", "Max field voltage (pu)"),
    ]

    fig, axes = plt.subplots(3, 1, figsize=(14, 11), sharex=True)

    for ax, (column, ylabel) in zip(axes, metrics):
        ax.bar(x, df[column], color=colors, alpha=0.9)
        ax.set_ylabel(ylabel)
        ax.grid(True, axis="y", alpha=0.3)

    axes[0].set_title("Transmission Scenario Generator Stress Metrics")
    axes[-1].set_xticks(x)
    axes[-1].set_xticklabels(df["label"], rotation=35, ha="right")

    fig.tight_layout()
    fig.savefig(out_dir / "transmission_scenario_generator_stress.png", dpi=150)
    plt.close(fig)


def save_overview_plot(df: pd.DataFrame, out_dir: Path):
    colors = scenario_colors(df)
    target_time = float(df["target_time"].max())
    x = np.arange(len(df))

    fig, axes = plt.subplots(2, 2, figsize=(14, 9))
    axes = axes.ravel()

    axes[0].bar(x, df["final_t"], color=colors, alpha=0.9)
    axes[0].axhline(target_time, color="black", linestyle="--", linewidth=1.0)
    axes[0].set_ylabel("Final time (s)")
    axes[0].set_title("Survival Time")
    axes[0].grid(True, axis="y", alpha=0.3)

    axes[1].bar(x, df["final_bus2_vmag"], color=colors, alpha=0.9)
    axes[1].set_ylabel("Final Bus 2 |V| (pu)")
    axes[1].set_title("Final Bus 2 Voltage")
    axes[1].grid(True, axis="y", alpha=0.3)

    axes[2].bar(x, df["delta_spread_deg"], color=colors, alpha=0.9)
    axes[2].set_ylabel("Delta spread (deg)")
    axes[2].set_title("Rotor-angle Spread")
    axes[2].grid(True, axis="y", alpha=0.3)

    axes[3].bar(x, df["omega_max_dev"], color=colors, alpha=0.9, label="omega max dev")
    axes[3].set_ylabel("Max |omega - 1| (pu)")
    axes[3].set_title("Speed Deviation")
    axes[3].grid(True, axis="y", alpha=0.3)

    for ax in axes:
        ax.set_xticks(x)
        ax.set_xticklabels(df["label"], rotation=35, ha="right")

    fig.suptitle("Standalone Transmission Scenario Overview", fontsize=18)
    fig.tight_layout()
    fig.savefig(out_dir / "transmission_scenario_overview.png", dpi=150)
    plt.close(fig)


def main():
    args = parse_args()
    results_path = args.results.resolve()
    out_dir = (args.out or results_path.parent).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    df = load_results(results_path)
    save_survival_plot(df, out_dir)
    save_bus2_plot(df, out_dir)
    save_stress_plot(df, out_dir)
    save_overview_plot(df, out_dir)

    stable_count = int(df["stable_to_target"].sum())
    print(f"Loaded {len(df)} scenarios from {results_path}")
    print(f"Stable to target: {stable_count}/{len(df)}")
    print(f"Saved plots to {out_dir}")


if __name__ == "__main__":
    main()
