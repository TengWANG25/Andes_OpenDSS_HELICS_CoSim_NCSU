#!/usr/bin/env python3
"""Run parallel FIDVR co-simulation cases for several Motor D shares."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "motor_share_sweep_runs"
DEFAULT_SHARES = "0,0.25,0.5,0.75,1.0"
DEFAULT_PORT_STRIDE = 10
DEFAULT_FREQUENCY_BASE_HZ = 60.0
MOTOR_SHARE_BASE_TOL = 1e-9
MOTOR_SHARE_BASE_COLOR = "0.05"
MOTOR_SHARE_BASE_LINESTYLE = (0, (6, 2.5))
MOTOR_SHARE_COLORS = (
    "#1f77b4",
    "#ff7f0e",
    "#2ca02c",
    "#d62728",
    "#9467bd",
    "#8c564b",
    "#e377c2",
    "#7f7f7f",
    "#bcbd22",
    "#17becf",
)
MOTOR_SHARE_LEGEND_FONTSIZE = 12
MOTOR_SHARE_LEGEND_TITLE_FONTSIZE = 13
SUMMARY_COLUMNS = [
    "motor_share",
    "label",
    "port",
    "exit_code",
    "run_dir",
    "run_log",
    "transmission_log",
    "feeder_log",
    "final_time_s",
    "min_tx_vmag_pu",
    "final_tx_vmag_pu",
    "min_dist_v_pu",
    "final_dist_v_pu",
    "max_p_total_pu",
    "max_q_total_pu",
]


@dataclass
class RunSpec:
    share: float
    label: str
    port: int
    run_dir: Path


@dataclass
class ActiveRun:
    spec: RunSpec
    process: subprocess.Popen
    log_handle: object
    started_at: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the FIDVR co-simulation for multiple FIDVR_MOTOR_SHARE values, "
            "store each case in its own folder, and generate overlay plots."
        )
    )
    parser.add_argument(
        "--shares",
        default=os.environ.get("FIDVR_MOTOR_SHARES", DEFAULT_SHARES),
        help=(
            "Comma or whitespace separated motor shares in [0, 1]. "
            f"Default: {DEFAULT_SHARES}"
        ),
    )
    parser.add_argument(
        "--step",
        type=float,
        default=None,
        help="Generate shares from 0 to 1 with this step, e.g. --step 0.1.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for run folders and overlay plots (default: {DEFAULT_OUTPUT_DIR.name}).",
    )
    parser.add_argument(
        "--base-port",
        type=int,
        default=25000,
        help="First HELICS broker port.",
    )
    parser.add_argument(
        "--port-stride",
        type=int,
        default=DEFAULT_PORT_STRIDE,
        help=(
            "Port spacing between parallel HELICS brokers. "
            f"Default: {DEFAULT_PORT_STRIDE}."
        ),
    )
    parser.add_argument(
        "--auto-base-port",
        action="store_true",
        help=(
            "If any requested broker port is already in use, move the whole "
            "sweep to the next free base-port block."
        ),
    )
    parser.add_argument(
        "--max-parallel",
        type=int,
        default=2,
        help="Maximum concurrent runs. Use 0 to run all requested shares at once.",
    )
    parser.add_argument(
        "--profile",
        default=os.environ.get("FIDVR_PROFILE", "weak_bus14"),
        help="FIDVR_PROFILE to use for every run (default: weak_bus14 or current env).",
    )
    parser.add_argument(
        "--run-sh",
        type=Path,
        default=SCRIPT_DIR / "run.sh",
        help="Path to run.sh.",
    )
    parser.add_argument(
        "--plot-only",
        action="store_true",
        help="Do not launch simulations; rebuild per-run and overlay plots from existing run folders.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip a case when its run folder already has a successful run.",
    )
    parser.add_argument(
        "--no-individual-plots",
        action="store_true",
        help="Skip calling plot_from_logs.py and plot_distribution_from_logs.py for each case.",
    )
    parser.add_argument(
        "--progress-interval",
        default=os.environ.get("PROGRESS_INTERVAL", "10"),
        help="Progress heartbeat interval passed to run.sh.",
    )
    parser.add_argument(
        "--target-time",
        type=float,
        default=None,
        help="Optional SIM target time override for every run.",
    )
    parser.add_argument(
        "--xlim",
        type=float,
        nargs=2,
        metavar=("XMIN", "XMAX"),
        default=None,
        help="Optional x-axis limits in seconds for generated plots.",
    )
    parser.add_argument(
        "--voltage-ylim",
        type=float,
        nargs=2,
        metavar=("YMIN", "YMAX"),
        default=None,
        help="Optional voltage y-axis limits in pu for generated plots.",
    )
    parser.add_argument(
        "--frequency-base-hz",
        type=float,
        default=float(os.environ.get("SYSTEM_FREQUENCY_HZ", DEFAULT_FREQUENCY_BASE_HZ)),
        help=f"Frequency base for omega-to-Hz plots (default: {DEFAULT_FREQUENCY_BASE_HZ:g} Hz).",
    )
    return parser.parse_args()


def parse_shares(args: argparse.Namespace) -> list[float]:
    if args.step is not None:
        if args.step <= 0.0 or args.step > 1.0:
            raise ValueError("--step must be in the interval (0, 1].")
        shares = []
        value = 0.0
        while value < 1.0 - 1e-9:
            shares.append(round(value, 10))
            value += args.step
        shares.append(1.0)
    else:
        parts = [part for part in re.split(r"[\s,]+", args.shares.strip()) if part]
        shares = [float(part) for part in parts]

    normalized = []
    seen = set()
    for share in shares:
        if share < -1e-12 or share > 1.0 + 1e-12:
            raise ValueError(f"Motor share {share} is outside [0, 1].")
        clipped = min(1.0, max(0.0, round(share, 10)))
        if clipped not in seen:
            normalized.append(clipped)
            seen.add(clipped)

    if not normalized:
        raise ValueError("No motor shares were requested.")
    return normalized


def format_share_label(share: float) -> str:
    text = f"{share:.4f}".rstrip("0").rstrip(".")
    if not text:
        text = "0"
    return f"share_{text.replace('.', 'p')}"


def format_share_percent_label(share: float) -> str:
    percent = share * 100.0
    if abs(percent - round(percent)) < 1e-9:
        text = f"{round(percent):.0f}"
    else:
        text = f"{percent:.4g}".rstrip("0").rstrip(".")
    return f"{text}%"


def is_base_motor_share(share: float) -> bool:
    return abs(share) <= MOTOR_SHARE_BASE_TOL


def format_motor_share_plot_label(share: float) -> str:
    label = format_share_percent_label(share)
    if is_base_motor_share(share):
        return f"{label} (base)"
    return label


def omega_pu_to_hz(series: pd.Series, frequency_base_hz: float) -> pd.Series:
    return series * frequency_base_hz


def bus_angle_frequency_deviation_hz(
    time_s: pd.Series,
    angle_rad: pd.Series,
) -> pd.DataFrame:
    data = pd.DataFrame(
        {
            "t_granted": pd.to_numeric(time_s, errors="coerce"),
            "angle_rad": pd.to_numeric(angle_rad, errors="coerce"),
        }
    ).dropna()
    data = data.drop_duplicates("t_granted", keep="last").sort_values("t_granted")
    if len(data) < 2:
        return pd.DataFrame(columns=["t_granted", "frequency_deviation_hz"])

    time = data["t_granted"].to_numpy(dtype=float)
    angle = np.unwrap(data["angle_rad"].to_numpy(dtype=float))
    if np.any(np.diff(time) <= 0.0):
        return pd.DataFrame(columns=["t_granted", "frequency_deviation_hz"])

    angular_speed = np.gradient(angle, time, edge_order=1)
    return pd.DataFrame(
        {
            "t_granted": time,
            "frequency_deviation_hz": angular_speed / (2.0 * np.pi),
        }
    )


def genrou_sort_key(label: str) -> tuple[int, str]:
    match = re.search(r"(\d+)$", label)
    if match:
        return int(match.group(1)), label
    return 10**9, label


def parse_andes_lst_omega_columns(lst_path: Path) -> dict[str, int]:
    if not lst_path.exists():
        return {}

    columns: dict[str, int] = {}
    pattern = re.compile(r"^\s*(?P<column>\d+),\s*omega\s+GENROU\s+(?P<gen>\S+)\s*,")
    for line in lst_path.read_text(errors="ignore").splitlines():
        match = pattern.match(line)
        if not match:
            continue
        columns[f"GENROU_{match.group('gen')}"] = int(match.group("column"))
    return dict(sorted(columns.items(), key=lambda item: genrou_sort_key(item[0])))


def load_genrou_frequency_series(
    spec: RunSpec,
    frequency_base_hz: float,
) -> dict[str, pd.DataFrame]:
    tx_csv = spec.run_dir / "transmission_timeseries.csv"
    if tx_csv.exists():
        tx = by_time_last(pd.read_csv(tx_csv))
        series: dict[str, pd.DataFrame] = {}
        for column in tx.columns:
            match = re.fullmatch(r"omega_(GENROU_\d+)_pu", column)
            if not match:
                continue
            label = match.group(1)
            data = tx[["t_granted", column]].dropna().rename(columns={column: "frequency_hz"})
            if data.empty:
                continue
            data["frequency_hz"] = omega_pu_to_hz(data["frequency_hz"], frequency_base_hz)
            series[label] = data
        if series:
            return dict(sorted(series.items(), key=lambda item: genrou_sort_key(item[0])))

    for npz_path in sorted(spec.run_dir.glob("*_out.npz")):
        lst_path = npz_path.with_suffix(".lst")
        omega_columns = parse_andes_lst_omega_columns(lst_path)
        if not omega_columns:
            continue
        with np.load(npz_path) as npz_data:
            if "data" not in npz_data:
                continue
            data = np.asarray(npz_data["data"], dtype=float)
        if data.ndim != 2 or data.shape[1] == 0:
            continue

        time = data[:, 0]
        series = {}
        for label, column_index in omega_columns.items():
            if column_index >= data.shape[1]:
                continue
            series[label] = pd.DataFrame(
                {
                    "t_granted": time,
                    "frequency_hz": data[:, column_index] * frequency_base_hz,
                }
            ).dropna()
        if series:
            return dict(sorted(series.items(), key=lambda item: genrou_sort_key(item[0])))

    return {}


def build_specs(args: argparse.Namespace, shares: list[float]) -> list[RunSpec]:
    if args.port_stride <= 0:
        raise ValueError("--port-stride must be a positive integer.")

    output_dir = args.output_dir.expanduser().resolve()
    return [
        RunSpec(
            share=share,
            label=format_share_label(share),
            port=args.base_port + index * args.port_stride,
            run_dir=output_dir / format_share_label(share),
        )
        for index, share in enumerate(shares)
    ]


def port_is_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", int(port))) == 0


def find_free_base_port(
    base_port: int,
    port_stride: int,
    run_count: int,
    port_is_occupied=port_is_listening,
) -> int:
    if run_count <= 0:
        return base_port
    last_start = 65535 - (run_count - 1) * port_stride
    for candidate in range(base_port, last_start + 1, port_stride):
        ports = [candidate + index * port_stride for index in range(run_count)]
        if all(not port_is_occupied(port) for port in ports):
            return candidate
    raise RuntimeError(
        "Could not find a free HELICS broker port block starting at "
        f"{base_port} with stride {port_stride} for {run_count} runs."
    )


def occupied_ports(specs: list[RunSpec]) -> list[tuple[str, int]]:
    return [(spec.label, spec.port) for spec in specs if port_is_listening(spec.port)]


def successful_run_exists(spec: RunSpec) -> bool:
    tx_csv = spec.run_dir / "transmission_timeseries.csv"
    run_log = spec.run_dir / "run.log"
    if not tx_csv.exists() or not run_log.exists():
        return False

    log_text = run_log.read_text(errors="ignore")
    return (
        "Simulation finished." in log_text
        and "Simulation finished with one or more process errors." not in log_text
    )


def clean_generated_outputs(spec: RunSpec) -> None:
    if not spec.run_dir.exists():
        return

    for pattern in (
        "*.log",
        "transmission_timeseries.csv",
        "bus*_fidvr_alerts.csv",
        "feeder_*_fidvr_alerts.csv",
        "*_out.lst",
        "*_out.npz",
        "*_out.txt",
        "*_out.csv",
        "*_out.npy",
    ):
        for path in spec.run_dir.glob(pattern):
            if path.is_file():
                path.unlink()

    plots_dir = spec.run_dir / "plots"
    if plots_dir.exists():
        shutil.rmtree(plots_dir)


def start_run(args: argparse.Namespace, spec: RunSpec) -> ActiveRun:
    spec.run_dir.mkdir(parents=True, exist_ok=True)
    clean_generated_outputs(spec)
    run_log = spec.run_dir / "run.log"
    env = os.environ.copy()
    env.update(
        {
            "FIDVR_ENABLE": "1",
            "FIDVR_PROFILE": args.profile,
            "FIDVR_MOTOR_SHARE": f"{spec.share:.10g}",
            "PORT": str(spec.port),
            "HELICS_BROKER_URL": f"tcp://127.0.0.1:{spec.port}",
            "RUN_OUTPUT_DIR": str(spec.run_dir),
            "COSIM_OUTPUT_DIR": str(spec.run_dir),
            "PROGRESS_INTERVAL": str(args.progress_interval),
        }
    )
    if args.target_time is not None:
        env["TARGET_TIME"] = f"{args.target_time:.10g}"

    log_handle = run_log.open("w", encoding="utf-8")
    print(
        f"[RUN] {spec.label}: share={spec.share:.4g} port={spec.port} "
        f"out={spec.run_dir}"
    )
    process = subprocess.Popen(
        [str(args.run_sh.expanduser().resolve())],
        cwd=SCRIPT_DIR,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return ActiveRun(spec=spec, process=process, log_handle=log_handle, started_at=time.time())


def run_sweep(args: argparse.Namespace, specs: list[RunSpec]) -> dict[str, int]:
    max_parallel = args.max_parallel if args.max_parallel > 0 else len(specs)
    max_parallel = max(1, min(max_parallel, len(specs)))
    pending = list(specs)
    active: list[ActiveRun] = []
    exit_codes: dict[str, int] = {}

    while pending or active:
        while pending and len(active) < max_parallel:
            spec = pending.pop(0)
            if args.skip_existing and successful_run_exists(spec):
                print(f"[SKIP] {spec.label}: existing successful run")
                exit_codes[spec.label] = 0
                continue
            active.append(start_run(args, spec))

        time.sleep(1.0)

        still_active = []
        for item in active:
            exit_code = item.process.poll()
            if exit_code is None:
                still_active.append(item)
                continue

            item.log_handle.close()
            elapsed = time.time() - item.started_at
            exit_codes[item.spec.label] = int(exit_code)
            status = "OK" if exit_code == 0 else "FAIL"
            print(f"[{status}] {item.spec.label}: exit={exit_code} elapsed={elapsed:.1f}s")
        active = still_active

    return exit_codes


def run_plotter(script_name: str, log_path: Path, out_dir: Path, args: argparse.Namespace) -> None:
    command = [
        sys.executable,
        str(SCRIPT_DIR / script_name),
        "--log",
        str(log_path),
        "--out",
        str(out_dir),
    ]
    if args.xlim is not None:
        command.extend(["--xlim", str(args.xlim[0]), str(args.xlim[1])])
    if args.voltage_ylim is not None:
        command.extend(["--voltage-ylim", str(args.voltage_ylim[0]), str(args.voltage_ylim[1])])
    subprocess.run(command, cwd=SCRIPT_DIR, check=True)


def run_substation_plotter(run_dir: Path, out_dir: Path, args: argparse.Namespace) -> None:
    command = [
        sys.executable,
        str(SCRIPT_DIR / "plot_feeder_substation_voltages.py"),
        "--run-dir",
        str(run_dir),
        "--out",
        str(out_dir),
    ]
    if args.xlim is not None:
        command.extend(["--xlim", str(args.xlim[0]), str(args.xlim[1])])
    if args.voltage_ylim is not None:
        command.extend(["--voltage-ylim", str(args.voltage_ylim[0]), str(args.voltage_ylim[1])])
    subprocess.run(command, cwd=SCRIPT_DIR, check=True)


def make_individual_plots(args: argparse.Namespace, specs: list[RunSpec], exit_codes: dict[str, int]) -> None:
    if args.no_individual_plots:
        return

    for spec in specs:
        if exit_codes.get(spec.label, 0) != 0:
            continue
        tx_log = spec.run_dir / "transmission.log"
        feeder_logs = sorted(spec.run_dir.glob("feeder_*.log"))
        if not tx_log.exists() or not feeder_logs:
            print(f"[WARN] {spec.label}: missing logs, skipping individual plots")
            continue

        plot_dir = spec.run_dir / "plots"
        print(f"[PLOT] {spec.label}: individual plots")
        for feeder_log in feeder_logs:
            run_plotter("plot_distribution_from_logs.py", feeder_log, plot_dir, args)
        run_substation_plotter(spec.run_dir, plot_dir, args)
        run_plotter("plot_from_logs.py", tx_log, plot_dir, args)


def by_time_last(df: pd.DataFrame) -> pd.DataFrame:
    return df.groupby("t_granted", as_index=False).last().sort_values("t_granted")


def load_distribution_series(spec: RunSpec) -> pd.DataFrame | None:
    plotted_csv = spec.run_dir / "plots" / "feeder_1_distribution_voltage.csv"
    if plotted_csv.exists():
        df = pd.read_csv(plotted_csv)
    else:
        feeder_log = spec.run_dir / "feeder_1.log"
        if not feeder_log.exists():
            return None
        from plot_distribution_from_logs import parse_distribution_log

        try:
            parsed = parse_distribution_log(feeder_log)
        except RuntimeError as exc:
            print(f"[WARN] {spec.label}: could not parse feeder log for summary: {exc}")
            return None
        df = (
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

    if "vpos_pu" in df.columns and df["vpos_pu"].notna().any():
        df["sweep_dist_v_pu"] = df["vpos_pu"]
    elif "alert_v_pu" in df.columns and df["alert_v_pu"].notna().any():
        df["sweep_dist_v_pu"] = df["alert_v_pu"]
    elif "vavg_pu" in df.columns:
        df["sweep_dist_v_pu"] = df["vavg_pu"]
    else:
        return None
    return df


def collect_summary(specs: list[RunSpec], exit_codes: dict[str, int]) -> pd.DataFrame:
    rows = []
    for spec in specs:
        tx_csv = spec.run_dir / "transmission_timeseries.csv"
        tx_log = spec.run_dir / "transmission.log"
        feeder_log = spec.run_dir / "feeder_1.log"
        run_ok = exit_codes.get(spec.label, 0) == 0
        tx = (
            by_time_last(pd.read_csv(tx_csv))
            if run_ok and tx_csv.exists()
            else pd.DataFrame()
        )
        dist = load_distribution_series(spec) if run_ok else None

        rows.append(
            {
                "motor_share": spec.share,
                "label": spec.label,
                "port": spec.port,
                "exit_code": exit_codes.get(spec.label),
                "run_dir": str(spec.run_dir),
                "run_log": str(spec.run_dir / "run.log"),
                "transmission_log": str(tx_log),
                "feeder_log": str(feeder_log),
                "final_time_s": float(tx["t_granted"].max()) if not tx.empty else pd.NA,
                "min_tx_vmag_pu": float(tx["Vmag"].min()) if "Vmag" in tx else pd.NA,
                "final_tx_vmag_pu": float(tx["Vmag"].dropna().iloc[-1])
                if "Vmag" in tx and tx["Vmag"].notna().any()
                else pd.NA,
                "min_dist_v_pu": float(dist["sweep_dist_v_pu"].min())
                if dist is not None and dist["sweep_dist_v_pu"].notna().any()
                else pd.NA,
                "final_dist_v_pu": float(dist["sweep_dist_v_pu"].dropna().iloc[-1])
                if dist is not None and dist["sweep_dist_v_pu"].notna().any()
                else pd.NA,
                "max_p_total_pu": float(tx["P_total"].max()) if "P_total" in tx else pd.NA,
                "max_q_total_pu": float(tx["Q_total"].max()) if "Q_total" in tx else pd.NA,
            }
        )
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def apply_axes_options(ax, args: argparse.Namespace, voltage_axis: bool = False) -> None:
    if args.xlim is not None:
        ax.set_xlim(*args.xlim)
    if voltage_axis and args.voltage_ylim is not None:
        ax.set_ylim(*args.voltage_ylim)


def build_motor_share_color_map(specs: list[RunSpec]) -> dict[float, str]:
    non_base_shares = sorted(
        {spec.share for spec in specs if not is_base_motor_share(spec.share)}
    )
    return {
        share: MOTOR_SHARE_COLORS[index % len(MOTOR_SHARE_COLORS)]
        for index, share in enumerate(non_base_shares)
    }


def motor_share_plot_kwargs(
    spec: RunSpec,
    color_map: dict[float, str],
    linewidth: float = 1.8,
) -> dict:
    if is_base_motor_share(spec.share):
        return {
            "label": format_motor_share_plot_label(spec.share),
            "color": MOTOR_SHARE_BASE_COLOR,
            "linewidth": max(linewidth + 0.8, 2.6),
            "linestyle": MOTOR_SHARE_BASE_LINESTYLE,
            "zorder": 5,
        }
    return {
        "label": format_motor_share_plot_label(spec.share),
        "color": color_map.get(spec.share),
        "linewidth": linewidth,
        "linestyle": "-",
        "zorder": 3,
    }


def add_motor_share_legend(ax, loc: str = "best", ncol: int = 1) -> None:
    ax.legend(
        title="Motor share",
        fontsize=MOTOR_SHARE_LEGEND_FONTSIZE,
        title_fontsize=MOTOR_SHARE_LEGEND_TITLE_FONTSIZE,
        loc=loc,
        ncol=ncol,
        framealpha=0.9,
        handlelength=2.8,
    )


def detect_transmission_interface_bus(specs: list[RunSpec]) -> str | None:
    patterns = (
        re.compile(r"\binterface_bus=(?P<bus>\d+)\b"),
        re.compile(r"\btransmission interface bus:\s*(?P<bus>\d+)\b", re.IGNORECASE),
        re.compile(r"\bInterface bus\s+(?P<bus>\d+)\b"),
    )
    buses = set()
    for spec in specs:
        for path in (spec.run_dir / "transmission.log", spec.run_dir / "run.log"):
            if not path.exists():
                continue
            text = path.read_text(errors="ignore")
            for pattern in patterns:
                match = pattern.search(text)
                if match:
                    buses.add(match.group("bus"))
                    break
    if len(buses) == 1:
        return next(iter(buses))
    return None


def make_overlay_plots(
    args: argparse.Namespace,
    specs: list[RunSpec],
    exit_codes: dict[str, int],
) -> None:
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    usable_specs = [
        spec
        for spec in specs
        if exit_codes.get(spec.label, 0) == 0
        and (spec.run_dir / "transmission_timeseries.csv").exists()
    ]
    if not usable_specs:
        print("[WARN] No transmission_timeseries.csv files found; overlay plots skipped.")
        return

    color_map = build_motor_share_color_map(usable_specs)
    interface_bus = detect_transmission_interface_bus(usable_specs)
    transmission_voltage_ylabel = (
        f"Transmission interface bus {interface_bus} |V| (pu)"
        if interface_bus is not None
        else "Transmission interface |V| (pu)"
    )

    fig, ax = plt.subplots(figsize=(9.5, 5.5))
    for spec in usable_specs:
        tx = by_time_last(pd.read_csv(spec.run_dir / "transmission_timeseries.csv"))
        if "Vmag" in tx:
            ax.plot(
                tx["t_granted"],
                tx["Vmag"],
                **motor_share_plot_kwargs(spec, color_map, linewidth=1.8),
            )
    ax.set_xlabel("Time (s)")
    ax.set_ylabel(transmission_voltage_ylabel)
    ax.grid(True)
    add_motor_share_legend(ax)
    apply_axes_options(ax, args, voltage_axis=True)
    fig.tight_layout()
    fig.savefig(output_dir / "motor_share_transmission_voltage.png", dpi=300)
    plt.close(fig)

    bus_frequency_ylabel = (
        f"Transmission interface bus {interface_bus} frequency deviation (Hz)"
        if interface_bus is not None
        else "Transmission interface bus frequency deviation (Hz)"
    )
    bus_frequency_by_spec: dict[str, pd.DataFrame] = {}
    for spec in usable_specs:
        tx = by_time_last(pd.read_csv(spec.run_dir / "transmission_timeseries.csv"))
        if {"t_granted", "Vang_rad"}.issubset(tx.columns):
            bus_frequency = bus_angle_frequency_deviation_hz(tx["t_granted"], tx["Vang_rad"])
            if not bus_frequency.empty:
                bus_frequency_by_spec[spec.label] = bus_frequency

    if bus_frequency_by_spec:
        fig, ax = plt.subplots(figsize=(9.5, 5.5))
        for spec in usable_specs:
            bus_frequency = bus_frequency_by_spec.get(spec.label)
            if bus_frequency is None:
                continue
            ax.plot(
                bus_frequency["t_granted"],
                bus_frequency["frequency_deviation_hz"],
                **motor_share_plot_kwargs(spec, color_map, linewidth=1.7),
            )
        ax.axhline(0.0, color="0.25", linewidth=1.0, linestyle=":")
        ax.set_xlabel("Time (s)")
        ax.set_ylabel(bus_frequency_ylabel)
        ax.grid(True)
        add_motor_share_legend(ax)
        apply_axes_options(ax, args)
        fig.tight_layout()
        fig.savefig(output_dir / "motor_share_interface_frequency_deviation.png", dpi=300)
        if interface_bus is not None:
            fig.savefig(output_dir / f"motor_share_bus{interface_bus}_frequency_deviation.png", dpi=300)
        plt.close(fig)
    else:
        print("[WARN] No interface-bus angle data found; bus frequency deviation skipped.")

    genrou_frequency_by_spec = {
        spec.label: load_genrou_frequency_series(spec, args.frequency_base_hz)
        for spec in usable_specs
    }
    genrou_labels = sorted(
        {
            label
            for series_by_gen in genrou_frequency_by_spec.values()
            for label in series_by_gen
        },
        key=genrou_sort_key,
    )
    if bus_frequency_by_spec or genrou_labels:
        axis_count = 1 + len(genrou_labels)
        fig, axes = plt.subplots(
            axis_count,
            1,
            sharex=True,
            figsize=(10.0, max(5.5, 1.85 * axis_count + 1.2)),
        )
        if axis_count == 1:
            axes = [axes]

        bus_axis = axes[0]
        for spec in usable_specs:
            bus_frequency = bus_frequency_by_spec.get(spec.label)
            if bus_frequency is None:
                continue
            bus_axis.plot(
                bus_frequency["t_granted"],
                bus_frequency["frequency_deviation_hz"],
                **motor_share_plot_kwargs(spec, color_map, linewidth=1.5),
            )
        bus_axis.axhline(0.0, color="0.25", linewidth=1.0, linestyle=":")
        bus_axis.set_ylabel(
            f"Bus {interface_bus} Δf (Hz)"
            if interface_bus is not None
            else "Interface Δf (Hz)"
        )

        for axis, genrou_label in zip(axes[1:], genrou_labels):
            for spec in usable_specs:
                genrou_frequency = genrou_frequency_by_spec.get(spec.label, {}).get(genrou_label)
                if genrou_frequency is None:
                    continue
                axis.plot(
                    genrou_frequency["t_granted"],
                    genrou_frequency["frequency_hz"],
                    **motor_share_plot_kwargs(spec, color_map, linewidth=1.3),
                )
            axis.axhline(args.frequency_base_hz, color="0.25", linewidth=1.0, linestyle=":")
            axis.set_ylabel(f"{genrou_label.replace('_', ' ')}\nfrequency (Hz)")

        axes[-1].set_xlabel("Time (s)")
        for axis in axes:
            axis.grid(True)
            apply_axes_options(axis, args)

        handles, labels = [], []
        for axis in axes:
            handles, labels = axis.get_legend_handles_labels()
            if handles:
                break
        if handles:
            fig.legend(
                handles,
                labels,
                title="Motor share",
                loc="upper center",
                ncol=min(len(labels), 5),
                fontsize=MOTOR_SHARE_LEGEND_FONTSIZE,
                title_fontsize=MOTOR_SHARE_LEGEND_TITLE_FONTSIZE,
                framealpha=0.9,
                handlelength=2.8,
            )
        fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.96 if handles else 1.0))
        fig.savefig(output_dir / "motor_share_frequency_response.png", dpi=300)
        plt.close(fig)
    else:
        print("[WARN] No frequency data found; frequency response plot skipped.")

    for column, ylabel, filename in (
        ("P_total", "Total active power P (pu)", "motor_share_total_active_power.png"),
        ("Q_total", "Total reactive power Q (pu)", "motor_share_total_reactive_power.png"),
    ):
        fig, ax = plt.subplots(figsize=(9.5, 5.5))
        plotted_power = False
        for spec in usable_specs:
            tx = by_time_last(pd.read_csv(spec.run_dir / "transmission_timeseries.csv"))
            if column not in tx:
                continue
            ax.plot(
                tx["t_granted"],
                tx[column],
                **motor_share_plot_kwargs(spec, color_map, linewidth=1.7),
            )
            plotted_power = True
        if plotted_power:
            ax.set_xlabel("Time (s)")
            ax.set_ylabel(ylabel)
            ax.grid(True)
            add_motor_share_legend(ax)
            apply_axes_options(ax, args)
            fig.tight_layout()
            fig.savefig(output_dir / filename, dpi=300)
        else:
            print(f"[WARN] No {column} data found; {filename} skipped.")
        plt.close(fig)

    fig, ax = plt.subplots(figsize=(9.5, 5.5))
    plotted = False
    distribution_voltage_ylabel = "Distribution voltage (pu)"
    for spec in usable_specs:
        dist = load_distribution_series(spec)
        if dist is None:
            continue
        if "dist_bus" in dist.columns and dist["dist_bus"].notna().any():
            dist_bus = str(dist["dist_bus"].dropna().iloc[-1])
            if "vpos_pu" in dist.columns and dist["vpos_pu"].notna().any():
                distribution_voltage_ylabel = f"Distribution bus {dist_bus} |V1| (pu)"
            elif "vavg_pu" in dist.columns and dist["vavg_pu"].notna().any():
                distribution_voltage_ylabel = f"Distribution bus {dist_bus} average |V| (pu)"
        ax.plot(
            dist["t_granted"],
            dist["sweep_dist_v_pu"],
            **motor_share_plot_kwargs(spec, color_map, linewidth=1.8),
        )
        plotted = True
    if plotted:
        ax.set_xlabel("Time (s)")
        ax.set_ylabel(distribution_voltage_ylabel)
        ax.grid(True)
        add_motor_share_legend(ax)
        apply_axes_options(ax, args, voltage_axis=True)
        fig.tight_layout()
        fig.savefig(output_dir / "motor_share_distribution_voltage.png", dpi=300)
    else:
        print("[WARN] No feeder voltage data found; distribution overlay skipped.")
    plt.close(fig)


def main() -> None:
    args = parse_args()
    args.output_dir = args.output_dir.expanduser().resolve()
    args.run_sh = args.run_sh.expanduser().resolve()
    shares = parse_shares(args)

    if not args.plot_only and args.auto_base_port:
        requested_base_port = args.base_port
        args.base_port = find_free_base_port(
            args.base_port,
            args.port_stride,
            len(shares),
        )
        if args.base_port != requested_base_port:
            print(
                f"[INFO] Base port {requested_base_port} is busy; "
                f"using free base port {args.base_port}."
            )

    specs = build_specs(args, shares)

    if not args.run_sh.exists():
        raise FileNotFoundError(f"run.sh not found: {args.run_sh}")

    if not args.plot_only:
        busy_ports = occupied_ports(specs)
        if busy_ports:
            busy_text = ", ".join(
                f"{label}:{port}" for label, port in busy_ports
            )
            raise SystemExit(
                "Requested HELICS broker port(s) already in use: "
                f"{busy_text}. Stop the stale process(es), pass "
                "--auto-base-port, or choose another --base-port."
            )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    print("[INFO] Motor shares: " + ", ".join(f"{share:g}" for share in shares))
    print(f"[INFO] Output directory: {args.output_dir}")
    print(f"[INFO] FIDVR profile: {args.profile}")
    print(f"[INFO] Base port/stride: {args.base_port}/{args.port_stride}")
    print(f"[INFO] Max parallel: {'all' if args.max_parallel == 0 else args.max_parallel}")

    exit_codes = {spec.label: 0 for spec in specs}
    if not args.plot_only:
        exit_codes = run_sweep(args, specs)

    make_individual_plots(args, specs, exit_codes)
    make_overlay_plots(args, specs, exit_codes)
    summary = collect_summary(specs, exit_codes)
    summary_path = args.output_dir / "motor_share_sweep_summary.csv"
    summary.to_csv(summary_path, index=False)
    print(f"[OK] Saved summary CSV: {summary_path}")
    print(f"[OK] Saved overlay plots to: {args.output_dir}")

    failed = [label for label, code in exit_codes.items() if code not in (0, None)]
    if failed:
        raise SystemExit(f"Failed runs: {', '.join(failed)}")


if __name__ == "__main__":
    main()
