#!/usr/bin/env python3
"""
Quick small-signal stability check for the IEEE14 transmission case.

This is a thin IEEE14-focused wrapper around the generic
`system_stability_check.py` workflow. It defaults to the same transmission
workbook used by `Transmission.py` and writes a compact stability report plus
CSV outputs for the most critical modes.

Examples
--------
Base IEEE14 operating point:
    /home/teng/miniforge3/envs/cosim/bin/python ieee14_small_signal_check.py

Include the latest feeder-equivalent operating point from transmission_timeseries.csv:
    /home/teng/miniforge3/envs/cosim/bin/python ieee14_small_signal_check.py \
        --use-latest-distload

Manually add an interface load at bus 4:
    /home/teng/miniforge3/envs/cosim/bin/python ieee14_small_signal_check.py \
        --interface-bus 4 --distload-p 0.1073 --distload-q 0.0638
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import andes
import pandas as pd

from system_stability_check import (
    build_eigenvalue_table,
    build_unstable_mode_table,
    disable_built_in_toggles,
    resolve_distload,
    write_summary,
)


DEFAULT_CASE = Path(__file__).with_name("ieee14_fault.xlsx")
DEFAULT_OUTPUT_DIR = Path(__file__).with_name("IEEE14_Small_Signal_Check_Results")
DEFAULT_TIMESERIES = Path(__file__).with_name("transmission_timeseries.csv")


def get_default_interface_bus() -> int:
    raw = os.environ.get("TX_INTERFACE_BUS", "4").strip()
    try:
        parsed = int(raw)
    except ValueError:
        return 4
    return parsed if parsed > 0 else 4


def parse_args():
    parser = argparse.ArgumentParser(
        description="Quick ANDES small-signal stability check for ieee14_fault.xlsx."
    )
    parser.add_argument(
        "--case",
        type=Path,
        default=DEFAULT_CASE,
        help=f"IEEE14 workbook to check (default: {DEFAULT_CASE.name})",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=(
            "Output directory for summary and CSV reports "
            f"(default: {DEFAULT_OUTPUT_DIR.name})"
        ),
    )
    parser.add_argument(
        "--interface-bus",
        type=int,
        default=get_default_interface_bus(),
        help=(
            "Bus used for the optional feeder-equivalent DistLoad "
            "(default: TX_INTERFACE_BUS if set, else 4)"
        ),
    )
    parser.add_argument(
        "--keep-builtin-toggles",
        action="store_true",
        help=(
            "Keep any workbook Toggle events. By default they are disabled so the "
            "check uses the base operating point."
        ),
    )
    parser.add_argument(
        "--distload-p",
        type=float,
        default=None,
        help="Optional active-power DistLoad in pu, added at --interface-bus.",
    )
    parser.add_argument(
        "--distload-q",
        type=float,
        default=None,
        help="Optional reactive-power DistLoad in pu, added at --interface-bus.",
    )
    parser.add_argument(
        "--distload-from-timeseries",
        type=Path,
        default=None,
        help=(
            "Read the first row of a transmission_timeseries.csv-style file and add "
            "that P_total/Q_total at --interface-bus."
        ),
    )
    parser.add_argument(
        "--use-latest-distload",
        action="store_true",
        help=(
            "Shortcut for --distload-from-timeseries transmission_timeseries.csv "
            "in the repo root."
        ),
    )
    parser.add_argument(
        "--eig-tol",
        type=float,
        default=1e-6,
        help="Tolerance used to classify eigenvalues as positive/zero/negative.",
    )
    parser.add_argument(
        "--top-states",
        type=int,
        default=10,
        help="Number of participating states to save for each unstable mode.",
    )
    parser.add_argument(
        "--top-critical",
        type=int,
        default=12,
        help="Number of modes with the largest real parts to save in the preview CSV.",
    )
    return parser.parse_args()


def add_distload(ss, interface_bus: int, p_value: float, q_value: float):
    ss.PQ.add(
        idx="DistLoadSS",
        name="DistLoadSS",
        bus=interface_bus,
        p0=p_value,
        q0=q_value,
    )
    ss.PQ.config.p2p = 1.0
    ss.PQ.config.p2i = 0.0
    ss.PQ.config.p2z = 0.0
    ss.PQ.config.q2q = 1.0
    ss.PQ.config.q2i = 0.0
    ss.PQ.config.q2z = 0.0


def build_critical_mode_table(eig_df: pd.DataFrame, top_n: int) -> pd.DataFrame:
    if eig_df.empty:
        return eig_df.copy()
    return eig_df.head(max(1, top_n)).reset_index(drop=True)


def main():
    args = parse_args()
    if args.use_latest_distload and args.distload_from_timeseries is None:
        args.distload_from_timeseries = DEFAULT_TIMESERIES

    if args.interface_bus <= 0:
        raise ValueError(
            f"Invalid --interface-bus={args.interface_bus}. Expected a positive integer."
        )

    case_path = args.case.resolve()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not case_path.exists():
        raise FileNotFoundError(f"Case file not found: {case_path}")

    distload_p, distload_q, distload_source = resolve_distload(args)

    ss = andes.load(str(case_path), setup=False, default_config=True)
    built_in_toggles_disabled = not args.keep_builtin_toggles
    if built_in_toggles_disabled:
        disable_built_in_toggles(ss)

    if distload_p is not None and distload_q is not None:
        add_distload(ss, args.interface_bus, distload_p, distload_q)

    ss.setup()
    ss.PFlow.run()
    if not getattr(ss.PFlow, "converged", True):
        raise RuntimeError("Power flow did not converge. Small-signal check aborted.")

    ss.EIG.config.tol = args.eig_tol
    ok = ss.EIG.run()
    if not ok:
        raise RuntimeError("ANDES EIG.run() reported failure.")

    eig_df = build_eigenvalue_table(ss, args.eig_tol)
    critical_df = build_critical_mode_table(eig_df, args.top_critical)
    unstable_df = build_unstable_mode_table(ss, eig_df, args.top_states)

    eigenvalue_csv = out_dir / "small_signal_eigenvalues.csv"
    critical_csv = out_dir / "small_signal_critical_modes.csv"
    unstable_csv = out_dir / "small_signal_unstable_modes.csv"
    summary_txt = out_dir / "small_signal_summary.txt"

    eig_df.to_csv(eigenvalue_csv, index=False)
    critical_df.to_csv(critical_csv, index=False)
    unstable_df.to_csv(unstable_csv, index=False)

    max_real = float(eig_df["real_part"].max())
    n_unstable = int((eig_df["stability"] == "unstable").sum())
    n_zero = int((eig_df["stability"] == "zero").sum())
    n_stable = int((eig_df["stability"] == "stable").sum())
    is_small_signal_stable = n_unstable == 0
    critical_mode_idx = int(critical_df.iloc[0]["mode_index"]) if not critical_df.empty else -1

    summary_lines = [
        "IEEE14 Small-Signal Stability Check",
        f"case: {case_path}",
        f"interface_bus: {args.interface_bus}",
        f"built_in_toggles_disabled: {built_in_toggles_disabled}",
        f"distload_source: {distload_source or 'none'}",
        f"distload_p: {distload_p if distload_p is not None else 'none'}",
        f"distload_q: {distload_q if distload_q is not None else 'none'}",
        f"eig_tolerance: {args.eig_tol}",
        f"small_signal_stable: {is_small_signal_stable}",
        f"unstable_modes: {n_unstable}",
        f"zero_modes: {n_zero}",
        f"stable_modes: {n_stable}",
        f"largest_real_part: {max_real:.9f}",
        f"most_critical_mode_index: {critical_mode_idx}",
        f"eigenvalue_csv: {eigenvalue_csv}",
        f"critical_modes_csv: {critical_csv}",
        f"unstable_modes_csv: {unstable_csv}",
    ]
    write_summary(summary_txt, summary_lines)

    print("\n".join(summary_lines))
    if not critical_df.empty:
        preview_cols = [
            "mode_index",
            "real_part",
            "imag_part",
            "freq_hz",
            "damping_ratio",
            "stability",
        ]
        print("\nMost critical modes (largest real parts):")
        print(critical_df[preview_cols].to_string(index=False))

    if not unstable_df.empty:
        preview_cols = [
            "mode_index",
            "real_part",
            "imag_part",
            "freq_hz",
            "damping_ratio",
            "state_1",
            "state_1_pfactor",
            "state_2",
            "state_2_pfactor",
        ]
        preview_cols = [col for col in preview_cols if col in unstable_df.columns]
        print("\nTop unstable modes:")
        print(unstable_df[preview_cols].head(10).to_string(index=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise
