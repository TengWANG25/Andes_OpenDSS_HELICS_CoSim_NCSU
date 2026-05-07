#!/usr/bin/env python3
"""
Check small-signal stability of the transmission system with ANDES.

This script is intended for the standalone transmission workbook, without
starting HELICS or the distribution feeders. It can:
  - disable built-in workbook toggle events for a clean base-case check
  - optionally add the feeder-equivalent load at bus 2
  - run PFlow followed by EIG
  - save eigenvalue and unstable-mode participation reports

Examples
--------
Base workbook only:
    /home/teng/miniforge3/envs/cosim/bin/python system_stability_check.py

Base workbook plus the initial co-simulation load from transmission_timeseries.csv:
    /home/teng/miniforge3/envs/cosim/bin/python system_stability_check.py \
        --distload-from-timeseries transmission_timeseries.csv

Manually specify a bus-2 load:
    /home/teng/miniforge3/envs/cosim/bin/python system_stability_check.py \
        --distload-p 0.10717 --distload-q 0.01856
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import andes
import numpy as np
import pandas as pd


DEFAULT_CASE = Path(__file__).with_name("IEEE118_from_PDF.xlsx")
DEFAULT_OUTPUT_DIR = Path(__file__).with_name("small_signal_check_results")
DEFAULT_TIMESERIES = Path(__file__).with_name("transmission_timeseries.csv")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run an ANDES small-signal stability check on the transmission case."
    )
    parser.add_argument(
        "--case",
        type=Path,
        default=DEFAULT_CASE,
        help=f"ANDES workbook to check (default: {DEFAULT_CASE.name})",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory for reports (default: {DEFAULT_OUTPUT_DIR.name})",
    )
    parser.add_argument(
        "--keep-builtin-toggles",
        action="store_true",
        help=(
            "Keep any Toggle/Toggler events embedded in the workbook. By default "
            "they are disabled so the small-signal check uses the base operating point."
        ),
    )
    parser.add_argument(
        "--distload-p",
        type=float,
        default=None,
        help="Add a constant-P load at bus 2 with this active-power value in pu.",
    )
    parser.add_argument(
        "--distload-q",
        type=float,
        default=None,
        help="Add a constant-Q load at bus 2 with this reactive-power value in pu.",
    )
    parser.add_argument(
        "--distload-from-timeseries",
        type=Path,
        default=None,
        help=(
            "Read the first row of transmission_timeseries.csv and add that "
            "P_total/Q_total as a DistLoad at bus 2."
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
    return parser.parse_args()


def disable_built_in_toggles(ss):
    if hasattr(ss, "Toggle") and getattr(ss.Toggle, "n", 0) > 0:
        for i in range(ss.Toggle.n):
            ss.Toggle.u.v[i] = 0


def resolve_distload(args):
    if args.distload_from_timeseries is not None:
        csv_path = args.distload_from_timeseries.resolve()
        if not csv_path.exists():
            raise FileNotFoundError(f"Timeseries CSV not found: {csv_path}")
        df = pd.read_csv(csv_path)
        required = {"P_total", "Q_total"}
        missing = sorted(required - set(df.columns))
        if missing:
            raise ValueError(
                f"{csv_path} is missing required columns: {', '.join(missing)}"
            )
        if df.empty:
            raise ValueError(f"{csv_path} does not contain any rows.")
        return float(df.iloc[0]["P_total"]), float(df.iloc[0]["Q_total"]), str(csv_path)

    if args.distload_p is None and args.distload_q is None:
        return None, None, ""

    if args.distload_p is None or args.distload_q is None:
        raise ValueError(
            "Provide both --distload-p and --distload-q, or use --distload-from-timeseries."
        )

    return float(args.distload_p), float(args.distload_q), "manual"


def add_distload(ss, p_value: float, q_value: float):
    ss.PQ.add(idx="DistLoadSS", name="DistLoadSS", bus=2, p0=p_value, q0=q_value)
    ss.PQ.config.p2p = 1.0
    ss.PQ.config.p2i = 0.0
    ss.PQ.config.p2z = 0.0
    ss.PQ.config.q2q = 1.0
    ss.PQ.config.q2i = 0.0
    ss.PQ.config.q2z = 0.0


def damping_ratio(real_part: float, imag_part: float) -> float:
    denom = math.hypot(real_part, imag_part)
    if denom == 0.0:
        return math.nan
    return -real_part / denom


def mode_frequency_hz(imag_part: float) -> float:
    return abs(imag_part) / (2.0 * math.pi)


def build_eigenvalue_table(ss, tol: float):
    mu = np.asarray(ss.EIG.mu)
    rows = []
    for mode_idx, eig in enumerate(mu):
        real_part = float(np.real(eig))
        imag_part = float(np.imag(eig))
        if real_part > tol:
            stability = "unstable"
        elif abs(real_part) <= tol:
            stability = "zero"
        else:
            stability = "stable"

        rows.append(
            {
                "mode_index": int(mode_idx),
                "real_part": real_part,
                "imag_part": imag_part,
                "freq_hz": mode_frequency_hz(imag_part),
                "damping_ratio": damping_ratio(real_part, imag_part),
                "stability": stability,
            }
        )

    df = pd.DataFrame(rows)
    return df.sort_values(["real_part", "imag_part"], ascending=[False, False]).reset_index(drop=True)


def build_unstable_mode_table(ss, eig_df: pd.DataFrame, top_states: int):
    unstable = eig_df[eig_df["stability"] == "unstable"].copy()
    if unstable.empty:
        return unstable

    pfactors = np.asarray(ss.EIG.pfactors, dtype=float)
    state_names = np.asarray(ss.EIG.x_name, dtype=object)

    rows = []
    for _, row in unstable.iterrows():
        mode_idx = int(row["mode_index"])
        pf = np.abs(pfactors[:, mode_idx])
        order = np.argsort(pf)[::-1][:top_states]

        mode_row = row.to_dict()
        for rank, state_idx in enumerate(order, start=1):
            mode_row[f"state_{rank}"] = str(state_names[state_idx])
            mode_row[f"state_{rank}_pfactor"] = float(pf[state_idx])
        rows.append(mode_row)

    return pd.DataFrame(rows)


def write_summary(summary_path: Path, summary_lines):
    summary_path.write_text("\n".join(summary_lines) + "\n")


def main():
    args = parse_args()
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
        add_distload(ss, distload_p, distload_q)

    ss.setup()
    ss.PFlow.run()
    if not getattr(ss.PFlow, "converged", True):
        raise RuntimeError("Power flow did not converge. Small-signal check aborted.")

    ss.EIG.config.tol = args.eig_tol
    ok = ss.EIG.run()
    if not ok:
        raise RuntimeError("ANDES EIG.run() reported failure.")

    eig_df = build_eigenvalue_table(ss, args.eig_tol)
    unstable_df = build_unstable_mode_table(ss, eig_df, args.top_states)

    eigenvalue_csv = out_dir / "small_signal_eigenvalues.csv"
    unstable_csv = out_dir / "small_signal_unstable_modes.csv"
    summary_txt = out_dir / "small_signal_summary.txt"

    eig_df.to_csv(eigenvalue_csv, index=False)
    unstable_df.to_csv(unstable_csv, index=False)

    max_real = float(eig_df["real_part"].max())
    n_unstable = int((eig_df["stability"] == "unstable").sum())
    n_zero = int((eig_df["stability"] == "zero").sum())
    n_stable = int((eig_df["stability"] == "stable").sum())
    is_small_signal_stable = n_unstable == 0

    summary_lines = [
        "Small-Signal Stability Check",
        f"case: {case_path}",
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
        f"top_states_per_unstable_mode: {args.top_states}",
        f"eigenvalue_csv: {eigenvalue_csv}",
        f"unstable_modes_csv: {unstable_csv}",
    ]
    write_summary(summary_txt, summary_lines)

    print("\n".join(summary_lines))
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
