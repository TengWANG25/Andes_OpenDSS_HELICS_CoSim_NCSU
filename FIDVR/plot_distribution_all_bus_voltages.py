#!/usr/bin/env python3
"""Plot IEEE 13-node voltage traces from feeder all-bus voltage CSV files."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd


IEEE13_NODE_BUSES = (
    "650",
    "632",
    "633",
    "634",
    "645",
    "646",
    "671",
    "675",
    "680",
    "684",
    "611",
    "652",
    "692",
)

NODE_HIGHLIGHTS = {
    "650": {
        "label": "650 (upstream)",
        "color": "#111111",
        "linewidth": 3.1,
        "linestyle": (0, (6, 2.2)),
        "zorder": 8,
    },
    "652": {
        "label": "652 (downstream)",
        "color": "#c2185b",
        "linewidth": 3.1,
        "linestyle": "-",
        "zorder": 9,
    },
}

NODE_COLORS = (
    "#0072B2",
    "#E69F00",
    "#009E73",
    "#D55E00",
    "#CC79A7",
    "#56B4E9",
    "#F0E442",
    "#7F7F7F",
    "#9467BD",
    "#8C564B",
    "#17BECF",
    "#BCBD22",
    "#1B9E77",
    "#D95F02",
    "#7570B3",
)

FEEDER_ALL_BUS_CSV_RE = re.compile(r"feeder_(?P<index>\d+)_all_bus_voltages\.csv$")

METRIC_LABELS = {
    "vpos_or_avg": "Voltage (pu)",
    "vavg": "Average voltage (pu)",
    "vpos": "Positive-sequence voltage (pu)",
    "va": "Phase A voltage (pu)",
    "vb": "Phase B voltage (pu)",
    "vc": "Phase C voltage (pu)",
}


def feeder_index_from_csv(csv_path: Path) -> int | None:
    match = FEEDER_ALL_BUS_CSV_RE.search(csv_path.name)
    return int(match.group("index")) if match else None


def feeder_csv_sort_key(csv_path: Path) -> tuple[int, str]:
    index = feeder_index_from_csv(csv_path)
    return (index if index is not None else 10**9, csv_path.name)


def time_axis_seconds_or_hours(t_seconds: pd.Series):
    if t_seconds.nunique() >= 2 and (t_seconds.max() - t_seconds.min()) >= 3600:
        return t_seconds / 3600.0, "Time (hours)"
    return t_seconds, "Time (s)"


def parse_bus_filter(value: str) -> tuple[str, tuple[str, ...] | None]:
    normalized = value.strip()
    if not normalized or normalized.lower() == "ieee13":
        return "ieee13", IEEE13_NODE_BUSES
    if normalized.lower() in {"all", "*"}:
        return "all", None
    buses = tuple(token for token in normalized.replace(",", " ").split() if token)
    if not buses:
        raise ValueError("--buses/--nodes did not include any node names.")
    return "selected", buses


def metric_column(data: pd.DataFrame, metric: str) -> pd.Series:
    if metric == "vpos_or_avg":
        return data["vpos_pu"].where(data["vpos_pu"].notna(), data["vavg_pu"])
    if metric == "vavg":
        return data["vavg_pu"]
    if metric == "vpos":
        return data["vpos_pu"]
    if metric == "va":
        return data["va_pu"]
    if metric == "vb":
        return data["vb_pu"]
    if metric == "vc":
        return data["vc_pu"]
    raise ValueError(f"Unsupported metric: {metric}")


def bus_sort_key(bus: str, bus_order: tuple[str, ...] | None) -> tuple[int, str]:
    if bus_order is None:
        return (10**9, bus)
    try:
        return (bus_order.index(bus), bus)
    except ValueError:
        return (10**9, bus)


def node_plot_kwargs(bus: str, color_index: int) -> dict:
    if bus in NODE_HIGHLIGHTS:
        return NODE_HIGHLIGHTS[bus].copy()
    return {
        "label": bus,
        "color": NODE_COLORS[color_index % len(NODE_COLORS)],
        "linewidth": 1.7,
        "linestyle": "-",
        "alpha": 0.78,
        "zorder": 3,
    }


def find_all_bus_voltage_csvs(run_dir: Path) -> list[Path]:
    return sorted(run_dir.glob("feeder_*_all_bus_voltages.csv"), key=feeder_csv_sort_key)


def load_all_bus_voltage_series(run_dir: Path | None, csv_paths: list[Path]) -> pd.DataFrame:
    paths = list(csv_paths)
    if run_dir is not None:
        paths.extend(find_all_bus_voltage_csvs(run_dir))

    seen = set()
    unique_paths = []
    for path in paths:
        resolved = path.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_paths.append(resolved)

    if not unique_paths:
        location = str(run_dir) if run_dir is not None else "the requested inputs"
        raise FileNotFoundError(f"No feeder_*_all_bus_voltages.csv files found in {location}")

    frames = []
    for path in sorted(unique_paths, key=feeder_csv_sort_key):
        frame = pd.read_csv(path)
        if frame.empty:
            print(f"[WARN] Skipping empty all-bus voltage CSV: {path}")
            continue
        if "feeder" not in frame.columns:
            feeder = feeder_index_from_csv(path)
            frame.insert(0, "feeder", feeder if feeder is not None else 1)
        frame.insert(0, "source_csv", str(path))
        frames.append(frame)

    if not frames:
        raise RuntimeError("No all-bus voltage data could be parsed.")
    return pd.concat(frames, ignore_index=True)


def filtered_plot_data(
    data: pd.DataFrame,
    buses: tuple[str, ...] | None,
    metric: str,
    feeder: int | None,
) -> pd.DataFrame:
    filtered = data.copy()
    filtered["bus"] = filtered["bus"].astype(str)
    if feeder is not None:
        filtered = filtered.loc[filtered["feeder"].astype(int) == feeder].copy()
    if buses is not None:
        filtered = filtered.loc[filtered["bus"].isin(buses)].copy()
    if filtered.empty:
        raise RuntimeError("No voltage rows matched the requested feeder/node filter.")

    filtered["plot_v_pu"] = metric_column(filtered, metric)
    filtered = filtered.dropna(subset=["t_granted", "plot_v_pu"])
    if filtered.empty:
        raise RuntimeError(f"No finite voltage values found for metric '{metric}'.")

    return (
        filtered.sort_values(["feeder", "bus", "t_granted", "iter"])
        .groupby(["feeder", "bus", "t_granted"], as_index=False)
        .last()
        .sort_values(["feeder", "bus", "t_granted"])
    )


def plot_distribution_all_bus_voltages(
    data: pd.DataFrame,
    out_dir: Path,
    bus_scope: str,
    bus_order: tuple[str, ...] | None,
    metric: str = "vavg",
    feeder: int | None = None,
    x_limits: tuple[float, float] | None = None,
    voltage_y_limits: tuple[float, float] | None = None,
) -> list[tuple[Path, Path]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    filtered = filtered_plot_data(data, bus_order, metric, feeder)
    scope_token = "ieee13" if bus_scope == "ieee13" else "all" if bus_scope == "all" else "selected"
    outputs = []

    for feeder_value, group in filtered.groupby("feeder", sort=True):
        feeder_int = int(feeder_value)
        csv_path = out_dir / f"feeder_{feeder_int}_{scope_token}_node_voltages.csv"
        plot_path = out_dir / f"feeder_{feeder_int}_{scope_token}_node_voltages.png"
        group.rename(columns={"bus": "node"}).to_csv(csv_path, index=False)

        fig, ax = plt.subplots(figsize=(10.5, 6.0))
        buses = sorted(group["bus"].unique(), key=lambda bus: bus_sort_key(bus, bus_order))
        for color_index, bus in enumerate(buses):
            bus_group = group.loc[group["bus"] == bus].sort_values("t_granted")
            x, xlabel = time_axis_seconds_or_hours(bus_group["t_granted"])
            ax.plot(x, bus_group["plot_v_pu"], **node_plot_kwargs(bus, color_index))

        ax.set_xlabel(xlabel)
        ax.set_ylabel(METRIC_LABELS[metric])
        ax.grid(True)
        ax.legend(
            title="Node",
            ncol=2,
            fontsize=10.5,
            title_fontsize=12,
            framealpha=0.9,
            handlelength=3.0,
        )
        if x_limits is not None:
            ax.set_xlim(*x_limits)
        if voltage_y_limits is not None:
            ax.set_ylim(*voltage_y_limits)
        fig.tight_layout()
        fig.savefig(plot_path, dpi=300)
        plt.close(fig)
        outputs.append((csv_path, plot_path))

    return outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot all IEEE 13-node voltages from a feeder all-bus voltage CSV."
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=Path("."),
        help="Run folder containing feeder_*_all_bus_voltages.csv files.",
    )
    parser.add_argument(
        "--csv",
        action="append",
        type=Path,
        default=[],
        help="Specific feeder all-bus voltage CSV to include. May be supplied multiple times.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output folder. Defaults to RUN_DIR/plots.",
    )
    parser.add_argument(
        "--feeder",
        type=int,
        default=None,
        help="Optional feeder index to plot. Defaults to every feeder CSV found.",
    )
    parser.add_argument(
        "--buses",
        "--nodes",
        dest="buses",
        default="ieee13",
        help="Node set to plot: ieee13, all, or a comma/space-separated node list.",
    )
    parser.add_argument(
        "--metric",
        choices=sorted(METRIC_LABELS),
        default="vavg",
        help="Voltage metric to compare. Default: vavg.",
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
    csv_paths = [path.expanduser().resolve() for path in args.csv]
    out_dir = args.out.expanduser().resolve() if args.out else run_dir / "plots"
    bus_scope, bus_order = parse_bus_filter(args.buses)

    print(f"[INFO] Run dir: {run_dir}")
    print(f"[INFO] Out: {out_dir}")
    data = load_all_bus_voltage_series(run_dir, csv_paths)
    outputs = plot_distribution_all_bus_voltages(
        data,
        out_dir,
        bus_scope=bus_scope,
        bus_order=bus_order,
        metric=args.metric,
        feeder=args.feeder,
        x_limits=tuple(args.xlim) if args.xlim is not None else None,
        voltage_y_limits=tuple(args.voltage_ylim)
        if args.voltage_ylim is not None
        else None,
    )
    for csv_path, plot_path in outputs:
        print(f"[OK] Saved CSV: {csv_path}")
        print(f"[OK] Saved plot: {plot_path}")


if __name__ == "__main__":
    main()
