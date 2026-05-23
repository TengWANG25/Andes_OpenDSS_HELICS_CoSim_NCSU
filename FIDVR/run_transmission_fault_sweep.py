#!/usr/bin/env python3
"""Run co-simulation cases for several transmission-side disturbances."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd

from run_motor_share_sweep import (
    apply_axes_options,
    by_time_last,
    clean_generated_outputs,
    detect_transmission_interface_bus,
    find_free_base_port,
    occupied_ports,
    run_plotter,
    run_substation_plotter,
    successful_run_exists,
)


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "transmission_fault_sweep_runs"
DEFAULT_FAULT_BUSES = "14,13,3,2,1"
DEFAULT_LINE_TRIPS = "Line_13"
DEFAULT_PORT_STRIDE = 10
HIGHLIGHT_COLORS = ["#0072B2", "#D55E00", "#009E73", "#7E57C2", "#CC79A7", "#E69F00"]
OTHER_CASE_STYLE = {
    "color": "0.68",
    "linewidth": 1.0,
    "alpha": 0.28,
    "zorder": 1,
}

SUMMARY_COLUMNS = [
    "name",
    "label",
    "kind",
    "fault_bus",
    "line_idx",
    "fault_time_s",
    "fault_duration_s",
    "fault_rf",
    "fault_xf",
    "motor_share",
    "port",
    "exit_code",
    "run_dir",
    "run_log",
    "transmission_log",
    "feeder_log",
    "final_time_s",
    "min_tx_vmag_pu",
    "final_tx_vmag_pu",
    "min_substation_v_pu",
    "final_substation_v_pu",
    "min_dist_v_pu",
    "final_dist_v_pu",
]


@dataclass
class FaultScenario:
    name: str
    label: str
    kind: str
    fault_bus: int | None
    line_idx: str | None
    fault_time_s: float
    fault_duration_s: float
    fault_rf: float
    fault_xf: float
    target_time_s: float | None = None
    notes: str = ""


@dataclass
class FaultRunSpec:
    scenario: FaultScenario
    label: str
    port: int
    run_dir: Path


@dataclass
class ActiveFaultRun:
    spec: FaultRunSpec
    process: subprocess.Popen
    log_handle: object
    started_at: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the FIDVR co-simulation for multiple transmission-side bus "
            "faults and compare the response at the same feeder/substation."
        )
    )
    parser.add_argument(
        "--fault-buses",
        default=os.environ.get("TX_FAULT_SWEEP_BUSES", DEFAULT_FAULT_BUSES),
        help=(
            "Comma or whitespace separated bus numbers for bus-fault scenarios. "
            f"Default: {DEFAULT_FAULT_BUSES}. Use an empty string to skip bus faults."
        ),
    )
    parser.add_argument(
        "--line-trips",
        default=os.environ.get("TX_LINE_TRIP_SWEEP_LINES", DEFAULT_LINE_TRIPS),
        help=(
            "Comma or whitespace separated transmission line ids for temporary "
            "line-trip/reclose scenarios, e.g. Line_13,Line_16. Default: none."
        ),
    )
    parser.add_argument(
        "--scenarios",
        type=Path,
        default=None,
        help=(
            "Optional CSV with columns name,bus,start_time,clear_time,xf,rf. "
            "When provided, it replaces --fault-buses."
        ),
    )
    parser.add_argument(
        "--only",
        nargs="+",
        default=None,
        help="Run only the named scenarios from --scenarios.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for run folders and overlay plots (default: {DEFAULT_OUTPUT_DIR.name}).",
    )
    parser.add_argument("--base-port", type=int, default=26000, help="First HELICS broker port.")
    parser.add_argument(
        "--port-stride",
        type=int,
        default=DEFAULT_PORT_STRIDE,
        help=f"Port spacing between parallel HELICS brokers. Default: {DEFAULT_PORT_STRIDE}.",
    )
    parser.add_argument(
        "--auto-base-port",
        action="store_true",
        help="Move the whole sweep to the next free port block if any requested port is busy.",
    )
    parser.add_argument(
        "--max-parallel",
        type=int,
        default=2,
        help="Maximum concurrent runs. Use 0 to run all requested faults at once.",
    )
    parser.add_argument(
        "--profile",
        default=os.environ.get("FIDVR_PROFILE", "weak_bus14"),
        help="FIDVR_PROFILE to use for every run (default: weak_bus14 or current env).",
    )
    parser.add_argument(
        "--motor-share",
        type=float,
        default=float(os.environ.get("FIDVR_MOTOR_SHARE", "1.0")),
        help="Fixed FIDVR_MOTOR_SHARE used for every fault case (default: 1.0 or env).",
    )
    parser.add_argument(
        "--fault-time",
        type=float,
        default=float(os.environ.get("TX_FAULT_TIME", "1.0")),
        help="Fault start time for generated --fault-buses cases.",
    )
    parser.add_argument(
        "--fault-duration",
        type=float,
        default=float(os.environ.get("TX_FAULT_DURATION", "0.08")),
        help="Fault duration for generated --fault-buses cases.",
    )
    parser.add_argument(
        "--line-trip-time",
        type=float,
        default=None,
        help="Line-trip start time for generated --line-trips cases (default: --fault-time).",
    )
    parser.add_argument(
        "--line-trip-duration",
        type=float,
        default=None,
        help="Line-trip duration before reclose for generated --line-trips cases (default: --fault-duration).",
    )
    parser.add_argument(
        "--fault-rf",
        type=float,
        default=float(os.environ.get("TX_FAULT_RF", "0.0")),
        help="Fault resistance for generated --fault-buses cases.",
    )
    parser.add_argument(
        "--fault-xf",
        type=float,
        default=float(os.environ.get("TX_FAULT_XF", "0.3")),
        help="Fault reactance for generated --fault-buses cases.",
    )
    parser.add_argument(
        "--target-time",
        type=float,
        default=None,
        help="Optional target simulation time override for every case.",
    )
    parser.add_argument(
        "--feeder",
        type=int,
        default=1,
        help="Feeder index used for same-substation overlay plots.",
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
        help="Do not launch simulations; rebuild plots from existing run folders.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip a case when its run folder already has a successful run.",
    )
    parser.add_argument(
        "--no-individual-plots",
        action="store_true",
        help="Skip per-case plot_from_logs.py and plot_distribution_from_logs.py calls.",
    )
    parser.add_argument(
        "--progress-interval",
        default=os.environ.get("PROGRESS_INTERVAL", "10"),
        help="Progress heartbeat interval passed to run.sh.",
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
        "--legend-impedance",
        action="store_true",
        help="Include bus-fault rf/xf values in overlay plot legends.",
    )
    return parser.parse_args()


def sanitize_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip())
    return safe.strip("_") or "scenario"


def parse_enabled(value) -> bool:
    if pd.isna(value):
        return True
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def parse_optional_float(value, default: float | None = None) -> float | None:
    if pd.isna(value) or str(value).strip() == "":
        return default
    return float(value)


def parse_optional_int(value) -> int | None:
    if pd.isna(value) or str(value).strip() == "":
        return None
    return int(float(value))


def parse_bus_list(value: str) -> list[int]:
    buses = []
    seen = set()
    for part in re.split(r"[\s,]+", value.strip()):
        if not part:
            continue
        bus = int(part)
        if bus <= 0:
            raise ValueError(f"Invalid fault bus {bus}; bus numbers must be positive.")
        if bus not in seen:
            buses.append(bus)
            seen.add(bus)
    if not buses:
        raise ValueError("No fault buses were requested.")
    return buses


def parse_line_list(value: str) -> list[str]:
    lines = []
    seen = set()
    for part in re.split(r"[\s,]+", value.strip()):
        if not part:
            continue
        line = part.strip()
        if line not in seen:
            lines.append(line)
            seen.add(line)
    return lines


def scenario_from_bus(bus: int, args: argparse.Namespace) -> FaultScenario:
    if args.fault_duration <= 0.0:
        raise ValueError("--fault-duration must be positive.")
    if args.fault_time < 0.0:
        raise ValueError("--fault-time must be non-negative.")
    if args.fault_rf < 0.0 or args.fault_xf < 0.0:
        raise ValueError("--fault-rf and --fault-xf must be non-negative.")

    name = f"Bus{bus}_fault_{args.fault_duration:g}s_xf{args.fault_xf:g}"
    label = sanitize_name(name)
    return FaultScenario(
        name=name,
        label=label,
        kind="bus_fault",
        fault_bus=bus,
        line_idx=None,
        fault_time_s=args.fault_time,
        fault_duration_s=args.fault_duration,
        fault_rf=args.fault_rf,
        fault_xf=args.fault_xf,
        target_time_s=args.target_time,
    )


def scenario_from_line(line_idx: str, args: argparse.Namespace) -> FaultScenario:
    line_time = args.line_trip_time if args.line_trip_time is not None else args.fault_time
    line_duration = (
        args.line_trip_duration
        if args.line_trip_duration is not None
        else args.fault_duration
    )
    if line_duration <= 0.0:
        raise ValueError("--line-trip-duration must be positive.")
    if line_time < 0.0:
        raise ValueError("--line-trip-time must be non-negative.")

    name = f"{line_idx}_trip_{line_duration:g}s"
    label = sanitize_name(name)
    return FaultScenario(
        name=name,
        label=label,
        kind="line_trip",
        fault_bus=None,
        line_idx=line_idx,
        fault_time_s=line_time,
        fault_duration_s=line_duration,
        fault_rf=0.0,
        fault_xf=0.0,
        target_time_s=args.target_time,
    )


def read_scenarios(args: argparse.Namespace) -> list[FaultScenario]:
    if args.scenarios is None:
        scenarios = []
        if args.fault_buses.strip():
            scenarios.extend(scenario_from_bus(bus, args) for bus in parse_bus_list(args.fault_buses))
        scenarios.extend(scenario_from_line(line_idx, args) for line_idx in parse_line_list(args.line_trips))
        if not scenarios:
            raise ValueError("No fault buses or line trips were requested.")
        return scenarios

    path = args.scenarios.expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Scenario CSV not found: {path}")

    df = pd.read_csv(path)
    required = {"name", "kind", "start_time", "clear_time"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"Scenario CSV {path} is missing columns: {', '.join(missing)}")

    selected = set(args.only or [])
    scenarios = []
    for _, row in df.iterrows():
        name = str(row["name"]).strip()
        if not name:
            continue
        if selected and name not in selected:
            continue
        if not parse_enabled(row.get("enabled", 1)):
            continue

        kind = str(row.get("kind", "bus_fault")).strip().lower()
        if kind in {"fault", "bus"}:
            kind = "bus_fault"
        elif kind in {"line", "line_trip_reclose"}:
            kind = "line_trip"
        if kind not in {"bus_fault", "line_trip"}:
            raise ValueError(
                f"Scenario '{name}' has kind '{kind}'. "
                "Supported kinds: bus_fault, line_trip."
            )

        fault_bus = parse_optional_int(row.get("bus"))
        line_idx = "" if pd.isna(row.get("line_idx")) else str(row.get("line_idx")).strip()
        if not line_idx and not pd.isna(row.get("line")):
            line_idx = str(row.get("line")).strip()
        start_time = parse_optional_float(row.get("start_time"))
        clear_time = parse_optional_float(row.get("clear_time"))
        xf = parse_optional_float(row.get("xf"))
        rf = parse_optional_float(row.get("rf"), 0.0)
        target_time = (
            args.target_time
            if args.target_time is not None
            else parse_optional_float(row.get("target_time"), None)
        )
        if kind == "bus_fault" and (fault_bus is None or xf is None):
            raise ValueError(f"Scenario '{name}' has incomplete bus-fault settings.")
        if kind == "line_trip" and not line_idx:
            raise ValueError(f"Scenario '{name}' requires line_idx for line_trip.")
        if start_time is None or clear_time is None:
            raise ValueError(f"Scenario '{name}' requires start_time and clear_time.")
        duration = clear_time - start_time
        if duration <= 0.0:
            raise ValueError(f"Scenario '{name}' has clear_time <= start_time.")

        scenarios.append(
            FaultScenario(
                name=name,
                label=sanitize_name(name),
                kind=kind,
                fault_bus=fault_bus,
                line_idx=line_idx or None,
                fault_time_s=start_time,
                fault_duration_s=duration,
                fault_rf=0.0 if rf is None else rf,
                fault_xf=0.0 if xf is None else xf,
                target_time_s=target_time,
                notes="" if pd.isna(row.get("notes")) else str(row.get("notes")).strip(),
            )
        )

    if selected:
        found = {scenario.name for scenario in scenarios}
        missing_selected = sorted(selected - found)
        if missing_selected:
            raise ValueError(
                "Requested scenarios were not found or were disabled: "
                + ", ".join(missing_selected)
            )
    if not scenarios:
        raise ValueError(f"No enabled scenarios found in {path}.")
    return scenarios


def build_specs(args: argparse.Namespace, scenarios: list[FaultScenario]) -> list[FaultRunSpec]:
    if args.port_stride <= 0:
        raise ValueError("--port-stride must be a positive integer.")
    output_dir = args.output_dir.expanduser().resolve()
    return [
        FaultRunSpec(
            scenario=scenario,
            label=scenario.label,
            port=args.base_port + index * args.port_stride,
            run_dir=output_dir / scenario.label,
        )
        for index, scenario in enumerate(scenarios)
    ]


def start_run(args: argparse.Namespace, spec: FaultRunSpec) -> ActiveFaultRun:
    spec.run_dir.mkdir(parents=True, exist_ok=True)
    clean_generated_outputs(spec)
    scenario = spec.scenario
    run_log = spec.run_dir / "run.log"
    target_time = (
        args.target_time
        if args.target_time is not None
        else scenario.target_time_s
    )

    env = os.environ.copy()
    env.update(
        {
            "FIDVR_ENABLE": "1",
            "FIDVR_PROFILE": args.profile,
            "FIDVR_MOTOR_SHARE": f"{args.motor_share:.10g}",
            "PORT": str(spec.port),
            "HELICS_BROKER_URL": f"tcp://127.0.0.1:{spec.port}",
            "RUN_OUTPUT_DIR": str(spec.run_dir),
            "COSIM_OUTPUT_DIR": str(spec.run_dir),
            "PROGRESS_INTERVAL": str(args.progress_interval),
            "TX_ENABLE_DISTURBANCE": "1",
            "TX_EVENT_KIND": "line_trip_reclose"
            if scenario.kind == "line_trip"
            else "bus_fault",
            "TX_FAULT_BUS": str(scenario.fault_bus or 14),
            "TX_FAULT_TIME": f"{scenario.fault_time_s:.10g}",
            "TX_FAULT_DURATION": f"{scenario.fault_duration_s:.10g}",
            "TX_FAULT_RF": f"{scenario.fault_rf:.10g}",
            "TX_FAULT_XF": f"{scenario.fault_xf:.10g}",
        }
    )
    if scenario.kind == "line_trip":
        env.update(
            {
                "TX_LINE_TRIP_LINE": str(scenario.line_idx),
                "TX_LINE_TRIP_TIME": f"{scenario.fault_time_s:.10g}",
                "TX_LINE_TRIP_DURATION": f"{scenario.fault_duration_s:.10g}",
            }
        )
    if target_time is not None:
        env["TARGET_TIME"] = f"{target_time:.10g}"

    log_handle = run_log.open("w", encoding="utf-8")
    if scenario.kind == "line_trip":
        event_text = f"line={scenario.line_idx}"
    else:
        event_text = (
            f"bus={scenario.fault_bus} rf/xf={scenario.fault_rf:g}/{scenario.fault_xf:g}"
        )
    print(
        f"[RUN] {spec.label}: kind={scenario.kind} {event_text} "
        f"t={scenario.fault_time_s:g}s duration={scenario.fault_duration_s:g}s "
        f"motor_share={args.motor_share:g} port={spec.port} out={spec.run_dir}"
    )
    process = subprocess.Popen(
        [str(args.run_sh.expanduser().resolve())],
        cwd=SCRIPT_DIR,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return ActiveFaultRun(spec=spec, process=process, log_handle=log_handle, started_at=time.time())


def run_sweep(args: argparse.Namespace, specs: list[FaultRunSpec]) -> dict[str, int]:
    max_parallel = args.max_parallel if args.max_parallel > 0 else len(specs)
    max_parallel = max(1, min(max_parallel, len(specs)))
    pending = list(specs)
    active: list[ActiveFaultRun] = []
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


def make_individual_plots(
    args: argparse.Namespace,
    specs: list[FaultRunSpec],
    exit_codes: dict[str, int],
) -> None:
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
        run_all_bus_voltage_plotter(spec.run_dir, plot_dir, args)
        run_plotter("plot_from_logs.py", tx_log, plot_dir, args)


def run_all_bus_voltage_plotter(run_dir: Path, out_dir: Path, args: argparse.Namespace) -> None:
    if not list(run_dir.glob("feeder_*_all_bus_voltages.csv")):
        print(f"[WARN] {run_dir.name}: no feeder all-bus voltage CSV found.")
        return

    command = [
        sys.executable,
        str(SCRIPT_DIR / "plot_distribution_all_bus_voltages.py"),
        "--run-dir",
        str(run_dir),
        "--out",
        str(out_dir),
        "--feeder",
        str(args.feeder),
    ]
    if args.xlim is not None:
        command.extend(["--xlim", str(args.xlim[0]), str(args.xlim[1])])
    if args.voltage_ylim is not None:
        command.extend(["--voltage-ylim", str(args.voltage_ylim[0]), str(args.voltage_ylim[1])])
    subprocess.run(command, cwd=SCRIPT_DIR, check=True)


def load_feeder_series(spec: FaultRunSpec, feeder: int) -> pd.DataFrame | None:
    plotted_csv = spec.run_dir / "plots" / f"feeder_{feeder}_distribution_voltage.csv"
    if plotted_csv.exists():
        return pd.read_csv(plotted_csv)

    feeder_log = spec.run_dir / f"feeder_{feeder}.log"
    if not feeder_log.exists():
        return None

    from plot_distribution_from_logs import parse_distribution_log

    try:
        parsed = parse_distribution_log(feeder_log)
    except RuntimeError as exc:
        print(f"[WARN] {spec.label}: could not parse feeder log: {exc}")
        return None

    return (
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


def add_distribution_voltage_column(df: pd.DataFrame) -> pd.DataFrame | None:
    if "vpos_pu" in df.columns and df["vpos_pu"].notna().any():
        return df.assign(sweep_dist_v_pu=df["vpos_pu"])
    if "alert_v_pu" in df.columns and df["alert_v_pu"].notna().any():
        return df.assign(sweep_dist_v_pu=df["alert_v_pu"])
    if "vavg_pu" in df.columns and df["vavg_pu"].notna().any():
        return df.assign(sweep_dist_v_pu=df["vavg_pu"])
    return None


def scenario_legend_label(spec: FaultRunSpec, include_impedance: bool = False) -> str:
    scenario = spec.scenario
    if scenario.kind == "line_trip":
        line_idx = (scenario.line_idx or "line").replace("_", " ")
        return f"{line_idx} trip"
    if scenario.fault_bus is not None:
        label = f"Bus {scenario.fault_bus} fault"
        if include_impedance:
            label += f" (xf={scenario.fault_xf:g}"
            if abs(scenario.fault_rf) > 1e-12:
                label += f", rf={scenario.fault_rf:g}"
            label += ")"
        return label
    return scenario.name


def _line_number(spec: FaultRunSpec) -> str | None:
    line_idx = spec.scenario.line_idx
    if not line_idx:
        return None
    match = re.search(r"(\d+)", line_idx)
    return match.group(1) if match else None


def _first_unselected(
    specs: list[FaultRunSpec],
    selected_labels: set[str],
    predicate,
) -> FaultRunSpec | None:
    for spec in specs:
        if spec.label in selected_labels:
            continue
        if predicate(spec):
            return spec
    return None


def select_highlight_cases(specs: list[FaultRunSpec]) -> dict[str, tuple[str, str]]:
    """Pick a small representative set for readable fault-sweep overlays."""
    selected: dict[str, tuple[str, str]] = {}
    selected_labels: set[str] = set()

    choices = [
        (
            "interface bus",
            lambda spec: spec.scenario.kind == "bus_fault" and spec.scenario.fault_bus == 14,
        ),
        (
            "near interface",
            lambda spec: spec.scenario.kind == "bus_fault" and spec.scenario.fault_bus == 13,
        ),
        (
            "remote bus",
            lambda spec: spec.scenario.kind == "bus_fault" and spec.scenario.fault_bus == 3,
        ),
        (
            "remote bus",
            lambda spec: spec.scenario.kind == "bus_fault" and spec.scenario.fault_bus == 2,
        ),
        (
            "remote bus",
            lambda spec: spec.scenario.kind == "bus_fault" and spec.scenario.fault_bus == 1,
        ),
        (
            "line trip",
            lambda spec: spec.scenario.kind == "line_trip" and _line_number(spec) == "13",
        ),
    ]

    fallbacks = {
        "near interface": [
            lambda spec: spec.scenario.kind == "bus_fault" and spec.scenario.fault_bus == 9,
        ],
        "remote bus": [
            lambda spec: spec.scenario.kind == "bus_fault" and spec.scenario.fault_bus == 3,
            lambda spec: spec.scenario.kind == "bus_fault" and spec.scenario.fault_bus == 2,
            lambda spec: spec.scenario.kind == "bus_fault" and spec.scenario.fault_bus == 1,
            lambda spec: spec.scenario.kind == "bus_fault" and spec.scenario.fault_bus == 7,
            lambda spec: spec.scenario.kind == "bus_fault" and spec.scenario.fault_bus == 10,
            lambda spec: spec.scenario.kind == "bus_fault" and spec.scenario.fault_bus == 4,
            lambda spec: spec.scenario.kind == "bus_fault" and spec.scenario.fault_bus == 8,
            lambda spec: spec.scenario.kind == "bus_fault"
            and spec.scenario.fault_bus not in {14, 13, 9, 7, 8, 1, 2, 3, 10, 4},
        ],
        "line trip": [
            lambda spec: spec.scenario.kind == "line_trip" and _line_number(spec) == "16",
            lambda spec: spec.scenario.kind == "line_trip",
        ],
    }

    for role, predicate in choices:
        spec = _first_unselected(specs, selected_labels, predicate)
        if spec is None:
            for fallback in fallbacks.get(role, []):
                spec = _first_unselected(specs, selected_labels, fallback)
                if spec is not None:
                    break
        if spec is None:
            continue
        color = HIGHLIGHT_COLORS[len(selected) % len(HIGHLIGHT_COLORS)]
        selected[spec.label] = (role, color)
        selected_labels.add(spec.label)

    return selected


def plot_fault_case_line(
    ax,
    x,
    y,
    spec: FaultRunSpec,
    highlighted: dict[str, tuple[str, str]],
    other_label_used: bool,
    include_impedance: bool = False,
) -> bool:
    highlight = highlighted.get(spec.label)
    if highlight is None:
        ax.plot(
            x,
            y,
            label=None if other_label_used else "Other cases",
            **OTHER_CASE_STYLE,
        )
        return True

    role, color = highlight
    ax.plot(
        x,
        y,
        color=color,
        linewidth=2.6,
        alpha=0.98,
        zorder=3,
        label=f"{scenario_legend_label(spec, include_impedance)} ({role})",
    )
    return other_label_used


def add_highlight_legend(ax) -> None:
    handles, labels = ax.get_legend_handles_labels()
    pairs = list(zip(handles, labels))
    ordered = [pair for pair in pairs if pair[1] != "Other cases"]
    ordered.extend(pair for pair in pairs if pair[1] == "Other cases")
    if ordered:
        ax.legend(
            [handle for handle, _ in ordered],
            [label for _, label in ordered],
            title="Highlighted cases",
            loc="best",
        )


def make_overlay_plots(
    args: argparse.Namespace,
    specs: list[FaultRunSpec],
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
        print("[WARN] No successful transmission_timeseries.csv files found; overlays skipped.")
        return

    interface_bus = detect_transmission_interface_bus(usable_specs)
    transmission_ylabel = (
        f"Transmission interface bus {interface_bus} |V| (pu)"
        if interface_bus is not None
        else "Transmission interface |V| (pu)"
    )
    highlighted = select_highlight_cases(usable_specs)
    if highlighted:
        highlighted_names = ", ".join(
            scenario_legend_label(spec, args.legend_impedance)
            for spec in usable_specs
            if spec.label in highlighted
        )
        print(f"[INFO] Highlighted fault cases: {highlighted_names}")

    fig, ax = plt.subplots(figsize=(9.5, 5.5))
    other_label_used = False
    for spec in usable_specs:
        tx = by_time_last(pd.read_csv(spec.run_dir / "transmission_timeseries.csv"))
        if "Vmag" not in tx:
            continue
        other_label_used = plot_fault_case_line(
            ax,
            tx["t_granted"],
            tx["Vmag"],
            spec,
            highlighted,
            other_label_used,
            args.legend_impedance,
        )
    ax.set_xlabel("Time (s)")
    ax.set_ylabel(transmission_ylabel)
    ax.grid(True)
    add_highlight_legend(ax)
    apply_axes_options(ax, args, voltage_axis=True)
    fig.tight_layout()
    fig.savefig(output_dir / "fault_sweep_transmission_voltage.png", dpi=300)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(9.5, 5.5))
    plotted_substation = False
    other_label_used = False
    for spec in usable_specs:
        feeder = load_feeder_series(spec, args.feeder)
        if feeder is None or "source_v_pu" not in feeder:
            continue
        other_label_used = plot_fault_case_line(
            ax,
            feeder["t_granted"],
            feeder["source_v_pu"],
            spec,
            highlighted,
            other_label_used,
            args.legend_impedance,
        )
        plotted_substation = True
    if plotted_substation:
        ax.set_xlabel("Time (s)")
        ax.set_ylabel(f"Feeder {args.feeder} source/substation |V| (pu)")
        ax.grid(True)
        add_highlight_legend(ax)
        apply_axes_options(ax, args, voltage_axis=True)
        fig.tight_layout()
        fig.savefig(output_dir / "fault_sweep_feeder_substation_voltage.png", dpi=300)
    else:
        print("[WARN] No feeder source/substation voltage data found.")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(9.5, 5.5))
    plotted_distribution = False
    distribution_ylabel = "Distribution voltage (pu)"
    other_label_used = False
    for spec in usable_specs:
        feeder = load_feeder_series(spec, args.feeder)
        if feeder is None:
            continue
        dist = add_distribution_voltage_column(feeder)
        if dist is None:
            continue
        if "dist_bus" in dist.columns and dist["dist_bus"].notna().any():
            dist_bus = str(dist["dist_bus"].dropna().iloc[-1])
            if "vpos_pu" in dist.columns and dist["vpos_pu"].notna().any():
                distribution_ylabel = f"Distribution bus {dist_bus} |V1| (pu)"
            elif "vavg_pu" in dist.columns and dist["vavg_pu"].notna().any():
                distribution_ylabel = f"Distribution bus {dist_bus} average |V| (pu)"
        other_label_used = plot_fault_case_line(
            ax,
            dist["t_granted"],
            dist["sweep_dist_v_pu"],
            spec,
            highlighted,
            other_label_used,
            args.legend_impedance,
        )
        plotted_distribution = True
    if plotted_distribution:
        ax.set_xlabel("Time (s)")
        ax.set_ylabel(distribution_ylabel)
        ax.grid(True)
        add_highlight_legend(ax)
        apply_axes_options(ax, args, voltage_axis=True)
        fig.tight_layout()
        fig.savefig(output_dir / "fault_sweep_distribution_voltage.png", dpi=300)
    else:
        print("[WARN] No distribution target-bus voltage data found.")
    plt.close(fig)


def collect_summary(
    args: argparse.Namespace,
    specs: list[FaultRunSpec],
    exit_codes: dict[str, int],
) -> pd.DataFrame:
    rows = []
    for spec in specs:
        scenario = spec.scenario
        tx_csv = spec.run_dir / "transmission_timeseries.csv"
        tx_log = spec.run_dir / "transmission.log"
        feeder_log = spec.run_dir / f"feeder_{args.feeder}.log"
        run_ok = exit_codes.get(spec.label, 0) == 0
        tx = by_time_last(pd.read_csv(tx_csv)) if run_ok and tx_csv.exists() else pd.DataFrame()
        feeder = load_feeder_series(spec, args.feeder) if run_ok else None
        dist = add_distribution_voltage_column(feeder) if feeder is not None else None

        rows.append(
            {
                "name": scenario.name,
                "label": spec.label,
                "kind": scenario.kind,
                "fault_bus": scenario.fault_bus,
                "line_idx": scenario.line_idx,
                "fault_time_s": scenario.fault_time_s,
                "fault_duration_s": scenario.fault_duration_s,
                "fault_rf": scenario.fault_rf,
                "fault_xf": scenario.fault_xf,
                "motor_share": args.motor_share,
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
                "min_substation_v_pu": float(feeder["source_v_pu"].min())
                if feeder is not None and "source_v_pu" in feeder
                else pd.NA,
                "final_substation_v_pu": float(feeder["source_v_pu"].dropna().iloc[-1])
                if feeder is not None
                and "source_v_pu" in feeder
                and feeder["source_v_pu"].notna().any()
                else pd.NA,
                "min_dist_v_pu": float(dist["sweep_dist_v_pu"].min())
                if dist is not None and dist["sweep_dist_v_pu"].notna().any()
                else pd.NA,
                "final_dist_v_pu": float(dist["sweep_dist_v_pu"].dropna().iloc[-1])
                if dist is not None and dist["sweep_dist_v_pu"].notna().any()
                else pd.NA,
            }
        )
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def main() -> None:
    args = parse_args()
    args.output_dir = args.output_dir.expanduser().resolve()
    args.run_sh = args.run_sh.expanduser().resolve()

    if not 0.0 <= args.motor_share <= 1.0:
        raise ValueError("--motor-share must be in [0, 1].")
    scenarios = read_scenarios(args)

    if not args.plot_only and args.auto_base_port:
        requested_base_port = args.base_port
        args.base_port = find_free_base_port(
            args.base_port,
            args.port_stride,
            len(scenarios),
        )
        if args.base_port != requested_base_port:
            print(
                f"[INFO] Base port {requested_base_port} is busy; "
                f"using free base port {args.base_port}."
            )

    specs = build_specs(args, scenarios)

    if not args.run_sh.exists():
        raise FileNotFoundError(f"run.sh not found: {args.run_sh}")

    if not args.plot_only:
        busy_ports = occupied_ports(specs)
        if busy_ports:
            busy_text = ", ".join(f"{label}:{port}" for label, port in busy_ports)
            raise SystemExit(
                "Requested HELICS broker port(s) already in use: "
                f"{busy_text}. Stop the stale process(es), pass "
                "--auto-base-port, or choose another --base-port."
            )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    print("[INFO] Fault scenarios: " + ", ".join(scenario.name for scenario in scenarios))
    print(f"[INFO] Output directory: {args.output_dir}")
    print(f"[INFO] FIDVR profile: {args.profile}")
    print(f"[INFO] Fixed motor share: {args.motor_share:g}")
    print(f"[INFO] Compared feeder/substation: feeder {args.feeder}")
    print(f"[INFO] Base port/stride: {args.base_port}/{args.port_stride}")
    print(f"[INFO] Max parallel: {'all' if args.max_parallel == 0 else args.max_parallel}")

    exit_codes = {spec.label: 0 for spec in specs}
    if not args.plot_only:
        exit_codes = run_sweep(args, specs)

    make_individual_plots(args, specs, exit_codes)
    make_overlay_plots(args, specs, exit_codes)
    summary = collect_summary(args, specs, exit_codes)
    summary_path = args.output_dir / "transmission_fault_sweep_summary.csv"
    summary.to_csv(summary_path, index=False)
    print(f"[OK] Saved summary CSV: {summary_path}")
    print(f"[OK] Saved overlay plots to: {args.output_dir}")

    failed = [label for label, code in exit_codes.items() if code not in (0, None)]
    if failed:
        raise SystemExit(f"Failed runs: {', '.join(failed)}")


if __name__ == "__main__":
    main()
