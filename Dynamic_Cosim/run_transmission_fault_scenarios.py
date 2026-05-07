#!/usr/bin/env python3
"""
Run standalone transmission-only contingency scenarios against the ANDES case.

This script is intentionally separate from the HELICS co-simulation flow so you
can screen transmission-side disturbances without starting the broker or the
distribution feeders.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path

import andes
import numpy as np
import pandas as pd


DEFAULT_CASE = Path(__file__).with_name("IEEE118dynamic.xlsx")
DEFAULT_SCENARIOS = Path(__file__).with_name("transmission_fault_scenarios.csv")
DEFAULT_OUTPUT_DIR = Path(__file__).with_name("transmission_scenario_runs")

SUMMARY_COLUMNS = [
    "name",
    "kind",
    "stable_to_target",
    "final_t",
    "target_time",
    "tds_tstep",
    "built_in_toggles_disabled",
    "line_idx",
    "fault_bus",
    "event_bus1",
    "event_bus2",
    "start_time",
    "clear_time",
    "xf",
    "rf",
    "final_bus2_vmag",
    "final_bus2_vang_rad",
    "delta_min_idx",
    "delta_min_bus",
    "delta_max_idx",
    "delta_max_bus",
    "delta_spread_deg",
    "omega_min_idx",
    "omega_min_bus",
    "omega_max_idx",
    "omega_max_bus",
    "omega_max_dev",
    "vf_min_idx",
    "vf_min_bus",
    "vf_max_idx",
    "vf_max_bus",
    "vf_max_pu",
    "notes",
    "error",
]


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Run standalone transmission-only line trips and bus faults against "
            "the IEEE118dynamic ANDES case."
        )
    )
    parser.add_argument(
        "--case",
        type=Path,
        default=DEFAULT_CASE,
        help=f"ANDES case to run (default: {DEFAULT_CASE.name})",
    )
    parser.add_argument(
        "--scenarios",
        type=Path,
        default=DEFAULT_SCENARIOS,
        help=f"Scenario CSV to read (default: {DEFAULT_SCENARIOS.name})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for the summary CSV (default: {DEFAULT_OUTPUT_DIR.name})",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        default=None,
        help="Run only the named scenarios from the CSV.",
    )
    parser.add_argument(
        "--target-time",
        type=float,
        default=None,
        help="Override target simulation time for every scenario.",
    )
    parser.add_argument(
        "--tds-tstep",
        type=float,
        default=None,
        help="Override ANDES TDS time step for every scenario.",
    )
    parser.add_argument(
        "--keep-builtin-toggles",
        action="store_true",
        help=(
            "Keep any Toggle events already stored in the workbook. By default, "
            "they are disabled so your scenario CSV is the only disturbance source."
        ),
    )
    return parser.parse_args()


def sanitize_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip())
    return safe.strip("_") or "scenario"


def parse_enabled(value) -> bool:
    if pd.isna(value):
        return True
    text = str(value).strip().lower()
    return text not in {"0", "false", "no", "off"}


def parse_optional_float(value):
    if pd.isna(value) or str(value).strip() == "":
        return None
    return float(value)


def parse_optional_int(value):
    if pd.isna(value) or str(value).strip() == "":
        return None
    return int(float(value))


def read_scenarios(path: Path, target_override=None, tstep_override=None, only=None):
    df = pd.read_csv(path)
    required = {"name", "kind", "start_time"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(
            f"Scenario CSV {path} is missing required columns: {', '.join(missing)}"
        )

    selected = set(only or [])
    scenarios = []
    for _, row in df.iterrows():
        name = str(row["name"]).strip()
        if not name:
            continue
        if selected and name not in selected:
            continue
        if not parse_enabled(row.get("enabled", 1)):
            continue

        kind = str(row["kind"]).strip().lower()
        scenario = {
            "name": name,
            "safe_name": sanitize_name(name),
            "kind": kind,
            "target_time": float(target_override)
            if target_override is not None
            else float(row.get("target_time", 30.0)),
            "tds_tstep": float(tstep_override)
            if tstep_override is not None
            else float(row.get("tds_tstep", 0.001)),
            "line_idx": None if pd.isna(row.get("line_idx")) else str(row.get("line_idx")).strip(),
            "fault_bus": parse_optional_int(row.get("bus")),
            "start_time": float(row["start_time"]),
            "clear_time": parse_optional_float(row.get("clear_time")),
            "xf": parse_optional_float(row.get("xf")),
            "rf": parse_optional_float(row.get("rf")),
            "notes": "" if pd.isna(row.get("notes")) else str(row.get("notes")).strip(),
        }

        if scenario["target_time"] <= 0.0:
            raise ValueError(f"Scenario '{name}' has invalid target_time.")
        if scenario["tds_tstep"] <= 0.0:
            raise ValueError(f"Scenario '{name}' has invalid tds_tstep.")

        if kind == "line_trip":
            if not scenario["line_idx"]:
                raise ValueError(f"Scenario '{name}' requires line_idx for line_trip.")
        elif kind == "bus_fault":
            if scenario["fault_bus"] is None:
                raise ValueError(f"Scenario '{name}' requires bus for bus_fault.")
            if scenario["clear_time"] is None:
                raise ValueError(f"Scenario '{name}' requires clear_time for bus_fault.")
            if scenario["xf"] is None:
                raise ValueError(f"Scenario '{name}' requires xf for bus_fault.")
            if scenario["rf"] is None:
                scenario["rf"] = 0.0
        else:
            raise ValueError(
                f"Scenario '{name}' has unsupported kind '{kind}'. "
                "Supported kinds: line_trip, bus_fault."
            )

        scenarios.append(scenario)

    if selected:
        found = {scenario["name"] for scenario in scenarios}
        missing_selected = sorted(selected - found)
        if missing_selected:
            raise ValueError(
                "Requested scenarios were not found or were disabled in the CSV: "
                + ", ".join(missing_selected)
            )

    if not scenarios:
        raise ValueError(f"No enabled scenarios found in {path}.")

    return scenarios


def disable_built_in_toggles(ss):
    if hasattr(ss, "Toggle") and getattr(ss.Toggle, "n", 0) > 0:
        for i in range(ss.Toggle.n):
            ss.Toggle.u.v[i] = 0


def get_bus_voltage(ss, bus_idx: int):
    bus_uid = ss.Bus.idx2uid(bus_idx)
    vmag = float(ss.dae.y[ss.Bus.v.a[bus_uid]])
    vang = float(ss.dae.y[ss.Bus.a.a[bus_uid]])
    return vmag, vang


def get_genrou_diagnostics(ss):
    diag = {
        "delta_min_idx": None,
        "delta_min_bus": math.nan,
        "delta_max_idx": None,
        "delta_max_bus": math.nan,
        "delta_spread_deg": math.nan,
        "omega_min_idx": None,
        "omega_min_bus": math.nan,
        "omega_max_idx": None,
        "omega_max_bus": math.nan,
        "omega_max_dev": math.nan,
        "vf_min_idx": None,
        "vf_min_bus": math.nan,
        "vf_max_idx": None,
        "vf_max_bus": math.nan,
        "vf_max_pu": math.nan,
    }

    if not hasattr(ss, "GENROU") or getattr(ss.GENROU, "n", 0) <= 0:
        return diag

    delta = np.asarray(ss.GENROU.delta.v, dtype=float)
    omega = np.asarray(ss.GENROU.omega.v, dtype=float)
    vf = np.asarray(ss.GENROU.vf.v, dtype=float)
    gen_ids = np.asarray(ss.GENROU.idx.v, dtype=object)
    gen_buses = np.asarray(ss.GENROU.bus.v, dtype=int)

    if delta.size:
        delta_deg = np.rad2deg(delta)
        delta_min_pos = int(np.argmin(delta_deg))
        delta_max_pos = int(np.argmax(delta_deg))
        diag["delta_min_idx"] = str(gen_ids[delta_min_pos])
        diag["delta_min_bus"] = int(gen_buses[delta_min_pos])
        diag["delta_max_idx"] = str(gen_ids[delta_max_pos])
        diag["delta_max_bus"] = int(gen_buses[delta_max_pos])
        diag["delta_spread_deg"] = float(delta_deg[delta_max_pos] - delta_deg[delta_min_pos])

    if omega.size:
        omega_min_pos = int(np.argmin(omega))
        omega_max_pos = int(np.argmax(omega))
        diag["omega_min_idx"] = str(gen_ids[omega_min_pos])
        diag["omega_min_bus"] = int(gen_buses[omega_min_pos])
        diag["omega_max_idx"] = str(gen_ids[omega_max_pos])
        diag["omega_max_bus"] = int(gen_buses[omega_max_pos])
        diag["omega_max_dev"] = float(np.max(np.abs(omega - 1.0)))

    if vf.size:
        vf_min_pos = int(np.argmin(vf))
        vf_max_pos = int(np.argmax(vf))
        diag["vf_min_idx"] = str(gen_ids[vf_min_pos])
        diag["vf_min_bus"] = int(gen_buses[vf_min_pos])
        diag["vf_max_idx"] = str(gen_ids[vf_max_pos])
        diag["vf_max_bus"] = int(gen_buses[vf_max_pos])
        diag["vf_max_pu"] = float(vf[vf_max_pos])

    return diag


def add_scenario_event(ss, scenario):
    if scenario["kind"] == "line_trip":
        line_uid = ss.Line.idx2uid(scenario["line_idx"])
        bus1 = int(ss.Line.bus1.v[line_uid])
        bus2 = int(ss.Line.bus2.v[line_uid])
        ss.add(
            "Toggle",
            {
                "idx": f"Trip_{scenario['safe_name']}",
                "model": "Line",
                "dev": scenario["line_idx"],
                "t": scenario["start_time"],
            },
        )
        if scenario["clear_time"] is not None:
            ss.add(
                "Toggle",
                {
                    "idx": f"Reclose_{scenario['safe_name']}",
                    "model": "Line",
                    "dev": scenario["line_idx"],
                    "t": scenario["clear_time"],
                },
            )
        return {
            "line_idx": scenario["line_idx"],
            "fault_bus": math.nan,
            "event_bus1": bus1,
            "event_bus2": bus2,
        }

    ss.add(
        "Fault",
        {
            "idx": f"Fault_{scenario['safe_name']}",
            "bus": scenario["fault_bus"],
            "tf": scenario["start_time"],
            "tc": scenario["clear_time"],
            "xf": scenario["xf"],
            "rf": scenario["rf"],
        },
    )
    return {
        "line_idx": None,
        "fault_bus": int(scenario["fault_bus"]),
        "event_bus1": int(scenario["fault_bus"]),
        "event_bus2": math.nan,
    }


def run_scenario(case_path: Path, scenario, disable_toggles_flag: bool):
    ss = andes.load(str(case_path), setup=False, default_config=True)
    if disable_toggles_flag:
        disable_built_in_toggles(ss)

    event_meta = add_scenario_event(ss, scenario)

    try:
        ss.setup()
        ss.PFlow.run()
        if not getattr(ss.PFlow, "converged", True):
            return {
                **event_meta,
                "stable_to_target": False,
                "final_t": math.nan,
                "final_bus2_vmag": math.nan,
                "final_bus2_vang_rad": math.nan,
                "error": "Power flow did not converge.",
            }

        ss.TDS.config.tstep = scenario["tds_tstep"]
        ss.TDS.config.tf = scenario["target_time"]
        print(f"RUN {scenario['name']} [{scenario['kind']}]")
        ss.TDS.run()

        final_t = float(ss.dae.t)
        bus2_vmag, bus2_vang = get_bus_voltage(ss, 2)
        diag = get_genrou_diagnostics(ss)
        return {
            **event_meta,
            "stable_to_target": bool(final_t + 1e-9 >= scenario["target_time"]),
            "final_t": final_t,
            "final_bus2_vmag": bus2_vmag,
            "final_bus2_vang_rad": bus2_vang,
            "error": "",
            **diag,
        }
    except Exception as exc:  # pragma: no cover - defensive runtime reporting
        final_t = float(getattr(ss.dae, "t", math.nan))
        try:
            bus2_vmag, bus2_vang = get_bus_voltage(ss, 2)
        except Exception:
            bus2_vmag, bus2_vang = math.nan, math.nan
        return {
            **event_meta,
            "stable_to_target": False,
            "final_t": final_t,
            "final_bus2_vmag": bus2_vmag,
            "final_bus2_vang_rad": bus2_vang,
            "error": str(exc),
        }


def main():
    args = parse_args()
    case_path = args.case.resolve()
    scenarios_path = args.scenarios.resolve()
    output_dir = args.output_dir.resolve()

    if not case_path.exists():
        raise FileNotFoundError(f"Case file not found: {case_path}")
    if not scenarios_path.exists():
        raise FileNotFoundError(f"Scenario CSV not found: {scenarios_path}")

    scenarios = read_scenarios(
        scenarios_path,
        target_override=args.target_time,
        tstep_override=args.tds_tstep,
        only=args.only,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    disable_toggles_flag = not args.keep_builtin_toggles

    for scenario in scenarios:
        result = run_scenario(case_path, scenario, disable_toggles_flag)
        result_row = {
            "name": scenario["name"],
            "kind": scenario["kind"],
            "target_time": scenario["target_time"],
            "tds_tstep": scenario["tds_tstep"],
            "built_in_toggles_disabled": disable_toggles_flag,
            "start_time": scenario["start_time"],
            "clear_time": scenario["clear_time"],
            "xf": scenario["xf"],
            "rf": scenario["rf"],
            "notes": scenario["notes"],
            **result,
        }
        results.append(result_row)

    df = pd.DataFrame(results, columns=SUMMARY_COLUMNS)
    summary_path = output_dir / "transmission_scenario_results.csv"
    df.to_csv(summary_path, index=False)

    display = df[
        [
            "name",
            "kind",
            "stable_to_target",
            "final_t",
            "target_time",
            "final_bus2_vmag",
            "delta_spread_deg",
            "omega_max_dev",
            "vf_max_pu",
        ]
    ]
    print("\nRESULTS")
    print(display.to_string(index=False))
    print(f"\nSaved summary CSV to {summary_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise
