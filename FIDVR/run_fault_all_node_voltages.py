#!/usr/bin/env python3
"""Run one transmission disturbance and plot all IEEE 13-node feeder voltages."""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "fault_all_node_voltage_run"


def env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    return default if value is None or value.strip() == "" else float(value)


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return default if value is None or value.strip() == "" else int(float(value))


def first_env_token(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    return value.replace(",", " ").split()[0]


def port_is_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", int(port))) == 0


def choose_port(port: int, auto_port: bool) -> int:
    if not port_is_listening(port):
        return port
    if not auto_port:
        raise SystemExit(
            f"Requested HELICS broker port {port} is already in use. "
            "Stop the stale process, pass --auto-port, or choose another --port."
        )

    candidate = port + 1
    while candidate <= 65535:
        if not port_is_listening(candidate):
            print(f"[INFO] Port {port} is busy; using free port {candidate}.", flush=True)
            return candidate
        candidate += 1
    raise SystemExit(f"Could not find a free port above {port}.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run one FIDVR co-simulation fault case and plot all IEEE 13-node "
            "distribution node voltages together."
        )
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Run folder for logs, CSVs, and plots. Default: {DEFAULT_OUTPUT_DIR.name}.",
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
        help="Skip the simulation and rebuild the all-node voltage plot from an existing run folder.",
    )
    parser.add_argument(
        "--event-kind",
        choices=("bus_fault", "line_trip"),
        default=os.environ.get("TX_EVENT_KIND", "bus_fault").replace("_reclose", ""),
        help="Transmission disturbance type. Default: current TX_EVENT_KIND or bus_fault.",
    )
    parser.add_argument(
        "--fault-bus",
        type=int,
        default=env_int("TX_FAULT_BUS", env_int("TX_INTERFACE_BUS", 14)),
        help="Transmission bus for bus_fault. Default: TX_FAULT_BUS, TX_INTERFACE_BUS, or 14.",
    )
    parser.add_argument(
        "--line",
        default=first_env_token("TX_LINE_TRIP_LINE", first_env_token("TX_LINE_TRIP_LINES", "Line_13")),
        help="Transmission line id for line_trip, e.g. Line_13.",
    )
    parser.add_argument(
        "--fault-time",
        type=float,
        default=env_float("TX_FAULT_TIME", 1.0),
        help="Fault or line-trip start time in seconds.",
    )
    parser.add_argument(
        "--fault-duration",
        type=float,
        default=env_float("TX_FAULT_DURATION", 0.08),
        help="Fault or line-trip duration in seconds.",
    )
    parser.add_argument(
        "--fault-rf",
        type=float,
        default=env_float("TX_FAULT_RF", 0.0),
        help="Bus-fault resistance.",
    )
    parser.add_argument(
        "--fault-xf",
        type=float,
        default=env_float("TX_FAULT_XF", 0.3),
        help="Bus-fault reactance.",
    )
    parser.add_argument(
        "--target-time",
        type=float,
        default=None,
        help="Optional simulation stop time. Defaults to run.sh/profile settings.",
    )
    parser.add_argument(
        "--profile",
        default=os.environ.get("FIDVR_PROFILE", "weak_bus14"),
        help="FIDVR_PROFILE for the run. Default: weak_bus14 or current env.",
    )
    parser.add_argument(
        "--motor-share",
        type=float,
        default=None,
        help="Optional FIDVR_MOTOR_SHARE override. Defaults to run.sh/profile settings.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=env_int("PORT", 27000),
        help="HELICS broker port. Default: 27000 or current PORT.",
    )
    parser.add_argument(
        "--auto-port",
        action="store_true",
        help="Use the next free port if --port is busy.",
    )
    parser.add_argument(
        "--progress-interval",
        default=os.environ.get("PROGRESS_INTERVAL", "10"),
        help="Progress heartbeat interval passed to run.sh.",
    )
    parser.add_argument(
        "--feeder",
        type=int,
        default=1,
        help="Feeder index to plot. Default: 1.",
    )
    parser.add_argument(
        "--buses",
        "--nodes",
        dest="buses",
        default=os.environ.get("DIST_ALL_BUS_VOLTAGE_BUSES", "ieee13"),
        help="Distribution node set to log/plot: ieee13, all, or a comma/space node list.",
    )
    parser.add_argument(
        "--metric",
        choices=("vpos_or_avg", "vavg", "vpos", "va", "vb", "vc"),
        default="vavg",
        help="Voltage metric to plot. Default: vavg.",
    )
    parser.add_argument(
        "--xlim",
        type=float,
        nargs=2,
        metavar=("XMIN", "XMAX"),
        default=None,
        help="Optional x-axis limits in seconds for the all-node plot.",
    )
    parser.add_argument(
        "--voltage-ylim",
        type=float,
        nargs=2,
        metavar=("YMIN", "YMAX"),
        default=None,
        help="Optional y-axis limits in pu for the all-node plot.",
    )
    return parser.parse_args()


def build_run_env(args: argparse.Namespace, output_dir: Path, port: int) -> dict[str, str]:
    env = os.environ.copy()
    event_kind = "line_trip_reclose" if args.event_kind == "line_trip" else "bus_fault"
    env.update(
        {
            "FIDVR_ENABLE": "1",
            "FIDVR_PROFILE": args.profile,
            "PORT": str(port),
            "HELICS_BROKER_URL": f"tcp://127.0.0.1:{port}",
            "RUN_OUTPUT_DIR": str(output_dir),
            "COSIM_OUTPUT_DIR": str(output_dir),
            "PROGRESS_INTERVAL": str(args.progress_interval),
            "TX_ENABLE_DISTURBANCE": "1",
            "TX_EVENT_KIND": event_kind,
            "DIST_LOG_ALL_BUS_VOLTAGES": "1",
            "DIST_ALL_BUS_VOLTAGE_BUSES": args.buses,
        }
    )
    if args.target_time is not None:
        env["TARGET_TIME"] = f"{args.target_time:.10g}"
    if args.motor_share is not None:
        env["FIDVR_MOTOR_SHARE"] = f"{args.motor_share:.10g}"

    if args.event_kind == "line_trip":
        env.update(
            {
                "TX_LINE_TRIP_LINE": str(args.line),
                "TX_LINE_TRIP_TIME": f"{args.fault_time:.10g}",
                "TX_LINE_TRIP_DURATION": f"{args.fault_duration:.10g}",
            }
        )
    else:
        env.update(
            {
                "TX_FAULT_BUS": str(args.fault_bus),
                "TX_FAULT_TIME": f"{args.fault_time:.10g}",
                "TX_FAULT_DURATION": f"{args.fault_duration:.10g}",
                "TX_FAULT_RF": f"{args.fault_rf:.10g}",
                "TX_FAULT_XF": f"{args.fault_xf:.10g}",
            }
        )
    return env


def run_simulation(args: argparse.Namespace, output_dir: Path, port: int) -> None:
    args.run_sh = args.run_sh.expanduser().resolve()
    if not args.run_sh.exists():
        raise FileNotFoundError(f"run.sh not found: {args.run_sh}")

    output_dir.mkdir(parents=True, exist_ok=True)
    run_log = output_dir / "run.log"
    env = build_run_env(args, output_dir, port)
    if args.event_kind == "line_trip":
        event_text = f"line trip {args.line}"
    else:
        event_text = f"bus fault at transmission bus {args.fault_bus}"
    print(f"[RUN] {event_text} -> {output_dir}", flush=True)
    with run_log.open("w", encoding="utf-8") as handle:
        result = subprocess.run(
            [str(args.run_sh)],
            cwd=SCRIPT_DIR,
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    if result.returncode != 0:
        raise SystemExit(f"Run failed with exit={result.returncode}. See {run_log}")
    print(f"[OK] Run finished. Log: {run_log}", flush=True)


def make_all_node_plot(args: argparse.Namespace, output_dir: Path) -> None:
    plot_dir = output_dir / "plots"
    command = [
        sys.executable,
        str(SCRIPT_DIR / "plot_distribution_all_bus_voltages.py"),
        "--run-dir",
        str(output_dir),
        "--out",
        str(plot_dir),
        "--feeder",
        str(args.feeder),
        "--buses",
        str(args.buses),
        "--metric",
        str(args.metric),
    ]
    if args.xlim is not None:
        command.extend(["--xlim", str(args.xlim[0]), str(args.xlim[1])])
    if args.voltage_ylim is not None:
        command.extend(["--voltage-ylim", str(args.voltage_ylim[0]), str(args.voltage_ylim[1])])

    subprocess.run(command, cwd=SCRIPT_DIR, check=True)


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir.expanduser().resolve()
    port = choose_port(args.port, args.auto_port)

    if not args.plot_only:
        run_simulation(args, output_dir, port)
    make_all_node_plot(args, output_dir)
    print(f"[OK] All-node voltage plot saved under: {output_dir / 'plots'}", flush=True)


if __name__ == "__main__":
    main()
