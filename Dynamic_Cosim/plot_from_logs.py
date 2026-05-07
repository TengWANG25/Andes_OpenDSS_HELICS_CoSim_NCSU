#!/usr/bin/env python3
"""
Load transmission-side co-simulation data and plot:
  - Total P/Q vs time
  - Bus |V| vs time
  - Combined vertical subplots (shared x-axis)

Preferred data source order:
  1. transmission_timeseries.csv beside the provided log
  2. legacy transmission.log with detailed runtime rows
  3. reconstructed transmission-side series from feeder_*.log files

Usage:
  python3 plot_from_logs.py --log transmission.log
  python3 plot_from_logs.py --log /path/to/transmission.log --bus 2 --out ./figs
"""

import argparse
import math
import re
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd


TRANSMISSION_COLUMNS = [
    "iter",
    "t_granted",
    "state",
    "updated",
    "P_total",
    "Q_total",
    "Vmag",
    "Vang_rad",
]

OPTIONAL_TRANSMISSION_COLUMNS = [
    "event_line_idx",
    "event_bus1",
    "event_bus2",
    "event_line_status",
    "event_bus1_vmag",
    "event_bus1_vang_rad",
    "event_bus2_vmag",
    "event_bus2_vang_rad",
    "event_bus_angle_diff_deg",
    "delta_min_deg",
    "delta_max_deg",
    "delta_spread_deg",
    "delta_min_idx",
    "delta_min_bus",
    "delta_max_idx",
    "delta_max_bus",
    "omega_min_pu",
    "omega_max_pu",
    "omega_max_dev",
    "omega_min_idx",
    "omega_min_bus",
    "omega_max_idx",
    "omega_max_bus",
    "vf_min_pu",
    "vf_max_pu",
    "vf_min_idx",
    "vf_min_bus",
    "vf_max_idx",
    "vf_max_bus",
]

EXPECTED_FEEDERS = tuple(range(1, 11))
FEEDER_VOLTAGE_TOL = 5e-6
FEEDER_ANGLE_TOL_DEG = 5e-6


def parse_transmission_log(log_path: Path, bus: int = 2) -> pd.DataFrame:
    lines = log_path.read_text(errors="ignore").splitlines()

    re_header = re.compile(
        r"\[iter=(\d+)\]\s+t_granted=([0-9.+\-eE]+)s.*state=([A-Z_]+)"
    )
    re_total = re.compile(
        r"\[iter=(\d+)\s+t=([0-9.+\-eE]+)s\]\s*Total\s+Distribution\s+Load\s+"
        r"P=([\-0-9.+eE]+),\s*Q=([\-0-9.+eE]+)\s*\(updated=(\d+)/(\d+)\)"
    )
    re_vmag_ang = re.compile(
        rf"\[iter=(\d+)\s+t=([0-9.+\-eE]+)s\]\s*Bus{bus}\s+\|V\|=([0-9.+\-eE]+),\s*angle\(rad\)=([\-0-9.+eE]+)"
    )
    re_vmag_only = re.compile(
        rf"\[iter=(\d+)\s+t=([0-9.+\-eE]+)s\]\s*Bus{bus}\s+\|V\|=([0-9.+\-eE]+)"
    )

    data = {}
    it_to_tgranted = {}

    for line in lines:
        match = re_header.search(line)
        if match:
            iteration = int(match.group(1))
            t_granted = float(match.group(2))
            state = match.group(3)
            it_to_tgranted[iteration] = t_granted
            key = (iteration, t_granted)
            data.setdefault(key, {})
            data[key].update(
                {"iter": iteration, "t_granted": t_granted, "state": state}
            )
            continue

        match = re_total.search(line)
        if match:
            iteration = int(match.group(1))
            time_hint = float(match.group(2))
            total_p = float(match.group(3))
            total_q = float(match.group(4))
            updated = int(match.group(5))
            t_granted = it_to_tgranted.get(iteration, time_hint)
            key = (iteration, t_granted)
            data.setdefault(key, {})
            data[key].update(
                {
                    "iter": iteration,
                    "t_granted": t_granted,
                    "updated": updated,
                    "P_total": total_p,
                    "Q_total": total_q,
                }
            )
            continue

        match = re_vmag_ang.search(line)
        if match:
            iteration = int(match.group(1))
            time_hint = float(match.group(2))
            vmag = float(match.group(3))
            vang_rad = float(match.group(4))
            t_granted = it_to_tgranted.get(iteration, time_hint)
            key = (iteration, t_granted)
            data.setdefault(key, {})
            data[key].update(
                {
                    "iter": iteration,
                    "t_granted": t_granted,
                    "Vmag": vmag,
                    "Vang_rad": vang_rad,
                }
            )
            continue

        match = re_vmag_only.search(line)
        if match:
            iteration = int(match.group(1))
            time_hint = float(match.group(2))
            vmag = float(match.group(3))
            t_granted = it_to_tgranted.get(iteration, time_hint)
            key = (iteration, t_granted)
            data.setdefault(key, {})
            data[key].update(
                {"iter": iteration, "t_granted": t_granted, "Vmag": vmag}
            )

    df = pd.DataFrame(list(data.values()))
    if df.empty:
        raise RuntimeError(
            "Parsed 0 rows from the legacy transmission log format."
        )

    if "updated" not in df.columns:
        df["updated"] = pd.NA

    df = df.sort_values(["t_granted", "iter"]).reset_index(drop=True)
    return df


def load_transmission_timeseries(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    missing = [column for column in TRANSMISSION_COLUMNS if column not in df.columns]
    if missing:
        missing_str = ", ".join(missing)
        raise RuntimeError(
            f"Transmission CSV is missing required columns: {missing_str}"
        )

    return df.sort_values(["t_granted", "iter"]).reset_index(drop=True)


FEEDER_ROW_RE = re.compile(
    r"\[Feeder(?P<feeder>\d+)\]\s+iter=(?P<iter>\d+)\s+"
    r"t_granted=(?P<t_granted>[0-9.+\-eE]+)s.*?state=(?P<state>[A-Z_]+)\s+\|\s+"
    r"Vupdate=(?P<vupdate>True|False)\s+"
    r"V=(?P<source_v_pu>[0-9.+\-eE]+)\s+pu\s+"
    r"ang=(?P<source_ang_deg>[\-0-9.+eE]+)\s+deg.*?"
    r"Pub=(?P<P_total>[\-0-9.+eE]+)\+j(?P<Q_total>[\-0-9.+eE]+)\s+pu"
)


def parse_feeder_runtime_log(log_path: Path) -> pd.DataFrame:
    rows = []

    for line in log_path.read_text(errors="ignore").splitlines():
        match = FEEDER_ROW_RE.search(line)
        if not match:
            continue

        rows.append(
            {
                "feeder": int(match.group("feeder")),
                "iter": int(match.group("iter")),
                "t_granted": float(match.group("t_granted")),
                "state": match.group("state"),
                "vupdate": match.group("vupdate") == "True",
                "source_v_pu": float(match.group("source_v_pu")),
                "source_ang_deg": float(match.group("source_ang_deg")),
                "P_total": float(match.group("P_total")),
                "Q_total": float(match.group("Q_total")),
            }
        )

    if not rows:
        raise RuntimeError(
            f"{log_path.name} does not contain feeder runtime rows with Pub/V values."
        )

    return pd.DataFrame(rows).sort_values(["t_granted", "iter"]).reset_index(drop=True)


def select_settled_rows(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    return (
        df.assign(state_rank=(df["state"] == "NEXT_STEP").astype(int))
        .sort_values(group_cols + ["state_rank", "iter"])
        .groupby(group_cols, as_index=False)
        .last()
        .sort_values(group_cols)
        .drop(columns=["state_rank"])
        .reset_index(drop=True)
    )


def reconstruct_from_feeder_logs(log_path: Path) -> pd.DataFrame:
    feeder_logs = [log_path.parent / f"feeder_{idx}.log" for idx in EXPECTED_FEEDERS]
    missing_logs = [path.name for path in feeder_logs if not path.exists()]
    if missing_logs:
        missing_str = ", ".join(missing_logs)
        raise RuntimeError(
            f"Cannot reconstruct from feeder logs. Missing: {missing_str}"
        )

    feeder_frames = []
    malformed_logs = []
    for feeder_log in feeder_logs:
        try:
            feeder_frames.append(
                select_settled_rows(
                    parse_feeder_runtime_log(feeder_log),
                    ["feeder", "t_granted"],
                )
            )
        except RuntimeError as exc:
            malformed_logs.append(f"{feeder_log.name} ({exc})")

    if malformed_logs:
        malformed_str = "; ".join(malformed_logs)
        raise RuntimeError(
            f"Cannot reconstruct from feeder logs. Malformed logs: {malformed_str}"
        )

    combined = pd.concat(feeder_frames, ignore_index=True)

    feeder_counts = combined.groupby("t_granted")["feeder"].nunique()
    incomplete_times = feeder_counts[feeder_counts != len(EXPECTED_FEEDERS)]
    if not incomplete_times.empty:
        preview = ", ".join(f"{t:.3f}s" for t in incomplete_times.index[:5])
        raise RuntimeError(
            "Cannot reconstruct from feeder logs because some times do not have all "
            f"{len(EXPECTED_FEEDERS)} feeders present. Examples: {preview}"
        )

    def _pick_state(series: pd.Series) -> str:
        states = sorted(series.dropna().unique().tolist())
        if len(states) != 1:
            raise RuntimeError(
                f"Feeder states do not agree during reconstruction: {states}"
            )
        return states[0]

    def _pick_voltage(series: pd.Series, label: str, tol: float) -> float:
        max_delta = series.max() - series.min()
        if max_delta > tol:
            raise RuntimeError(
                f"Feeder {label} values do not agree within tolerance at one or more times."
            )
        return float(series.iloc[0])

    rows = []
    for t_granted, group in combined.groupby("t_granted", sort=True):
        source_v_pu = _pick_voltage(
            group["source_v_pu"], "source voltage magnitudes", FEEDER_VOLTAGE_TOL
        )
        source_ang_deg = _pick_voltage(
            group["source_ang_deg"], "source voltage angles", FEEDER_ANGLE_TOL_DEG
        )

        rows.append(
            {
                "iter": int(group["iter"].max()),
                "t_granted": float(t_granted),
                "state": _pick_state(group["state"]),
                "updated": int(group["feeder"].nunique()),
                "P_total": float(group["P_total"].sum()),
                "Q_total": float(group["Q_total"].sum()),
                "Vmag": source_v_pu,
                "Vang_rad": math.radians(source_ang_deg),
            }
        )

    return pd.DataFrame(rows, columns=TRANSMISSION_COLUMNS).sort_values(
        ["t_granted", "iter"]
    ).reset_index(drop=True)


def load_transmission_plot_data(log_path: Path, bus: int = 2) -> pd.DataFrame:
    csv_path = log_path.with_name("transmission_timeseries.csv")
    if csv_path.exists():
        print(f"[INFO] Data source: transmission CSV ({csv_path.name})")
        return load_transmission_timeseries(csv_path)

    legacy_error = None
    try:
        df = parse_transmission_log(log_path, bus=bus)
        print(f"[INFO] Data source: legacy transmission log ({log_path.name})")
        return df
    except RuntimeError as exc:
        legacy_error = exc

    try:
        df = reconstruct_from_feeder_logs(log_path)
        print("[INFO] Data source: reconstructed from feeder logs")
        return df
    except RuntimeError as feeder_exc:
        raise RuntimeError(
            "Unable to load transmission-side plot data.\n"
            f"Legacy transmission log parse failed: {legacy_error}\n"
            f"Feeder reconstruction failed: {feeder_exc}"
        ) from feeder_exc


def _time_axis_seconds_or_hours(t_seconds: pd.Series):
    if t_seconds.nunique() >= 2 and (t_seconds.max() - t_seconds.min()) >= 3600:
        return t_seconds / 3600.0, "Time (hours)"
    return t_seconds, "Time (s)"


def _dropna_if_present(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    missing = [column for column in columns if column not in df.columns]
    if missing:
        return pd.DataFrame()
    return df.dropna(subset=columns)


def _first_valid_value(df: pd.DataFrame, column: str):
    if column not in df.columns:
        return None
    series = df[column].dropna()
    if series.empty:
        return None
    return series.iloc[0]


def _apply_zoom_ylim(ax, series_list, pad_ratio: float = 0.1, min_pad: float = 1e-4):
    values = []
    for series in series_list:
        if series is None:
            continue
        arr = pd.to_numeric(pd.Series(series), errors="coerce").to_numpy(dtype=float)
        arr = arr[np.isfinite(arr)]
        if arr.size:
            values.append(arr)

    if not values:
        return

    all_values = np.concatenate(values)
    vmin = float(np.min(all_values))
    vmax = float(np.max(all_values))
    span = vmax - vmin
    if span <= 0.0:
        pad = max(min_pad, abs(vmax) * pad_ratio, 1e-6)
    else:
        pad = max(min_pad, span * pad_ratio)
    ax.set_ylim(vmin - pad, vmax + pad)


def _format_genrou_identity(row: pd.Series, idx_col: str, bus_col: str) -> str:
    idx = row.get(idx_col)
    bus = row.get(bus_col)
    if pd.isna(idx) or idx is None:
        return "n/a"
    if pd.isna(bus):
        return str(idx)
    return f"{idx} @ bus {int(bus)}"


def make_plots(df: pd.DataFrame, out_dir: Path, bus: int = 2):
    out_dir.mkdir(parents=True, exist_ok=True)

    by_t = df.groupby("t_granted", as_index=False).last().sort_values("t_granted")

    n_total = by_t[["P_total", "Q_total"]].dropna().shape[0]
    n_vmag = by_t[["Vmag"]].dropna().shape[0]
    n_vang = by_t[["Vang_rad"]].dropna().shape[0] if "Vang_rad" in by_t.columns else 0
    print(f"[INFO] Unique t_granted: {by_t['t_granted'].nunique()}")
    print(f"[INFO] Points with P/Q: {n_total}")
    print(f"[INFO] Points with Vmag: {n_vmag}")
    print(f"[INFO] Points with Vang_rad: {n_vang}")

    x, xlabel = _time_axis_seconds_or_hours(by_t["t_granted"])
    extra_pngs = []

    d_pq = by_t.dropna(subset=["P_total", "Q_total"])
    plt.figure()
    if len(d_pq):
        x_pq, _ = _time_axis_seconds_or_hours(d_pq["t_granted"])
        plt.plot(x_pq, d_pq["P_total"], label="P_total (pu)")
        plt.plot(x_pq, d_pq["Q_total"], label="Q_total (pu)")
        plt.legend()
        _apply_zoom_ylim(plt.gca(), [d_pq["P_total"], d_pq["Q_total"]], min_pad=1e-5)
    plt.xlabel(xlabel)
    plt.ylabel("Total Distribution Load (pu)")
    plt.title("Total Distribution Load vs Time")
    plt.tight_layout()
    plt.savefig(out_dir / "total_pq_vs_time.png", dpi=300)
    plt.close()

    d_v = by_t.dropna(subset=["Vmag"])
    plt.figure()
    if len(d_v):
        x_v, _ = _time_axis_seconds_or_hours(d_v["t_granted"])
        plt.plot(x_v, d_v["Vmag"])
        _apply_zoom_ylim(plt.gca(), [d_v["Vmag"]], min_pad=5e-4)
    plt.xlabel(xlabel)
    plt.ylabel(f"Bus {bus} Voltage Magnitude |V| (pu)")
    plt.title(f"Bus {bus} Voltage Magnitude vs Time")
    plt.tight_layout()
    plt.savefig(out_dir / f"bus{bus}_voltage_vs_time.png", dpi=300)
    plt.close()

    d_ang = _dropna_if_present(by_t, ["Vang_rad"])
    if len(d_ang):
        plt.figure()
        x_ang, _ = _time_axis_seconds_or_hours(d_ang["t_granted"])
        plt.plot(x_ang, d_ang["Vang_rad"] * 180.0 / math.pi)
        plt.xlabel(xlabel)
        plt.ylabel(f"Bus {bus} Voltage Angle (deg)")
        plt.title(f"Bus {bus} Voltage Angle vs Time")
        plt.tight_layout()
        angle_png = out_dir / f"bus{bus}_angle_vs_time.png"
        plt.savefig(angle_png, dpi=300)
        plt.close()
        extra_pngs.append(angle_png.name)

    fig, ax = plt.subplots(2, 1, sharex=True, figsize=(8.5, 6.5))
    if len(d_pq):
        x_pq, _ = _time_axis_seconds_or_hours(d_pq["t_granted"])
        ax[0].plot(x_pq, d_pq["P_total"], label="P_total (pu)")
        ax[0].plot(x_pq, d_pq["Q_total"], label="Q_total (pu)")
        _apply_zoom_ylim(ax[0], [d_pq["P_total"], d_pq["Q_total"]], min_pad=1e-5)
        ax[0].set_ylabel("Total Load (pu)")
        ax[0].legend()
    ax[0].set_title("Aggregated feeder load")
    ax[0].grid(True)

    if len(d_v):
        x_v, _ = _time_axis_seconds_or_hours(d_v["t_granted"])
        ax[1].plot(x_v, d_v["Vmag"], label=f"Bus {bus} |V|")
        ax[1].set_ylabel(f"Bus {bus} |V| (pu)")
        _apply_zoom_ylim(ax[1], [d_v["Vmag"]], min_pad=5e-4)
        ax[1].set_title(f"Transmission bus {bus} voltage")
        ax[1].legend()
    ax[1].set_xlabel(xlabel)
    ax[1].grid(True)

    fig.suptitle("ANDES-OpenDSS Co-simulation: Load and Voltage vs Time", fontsize=14)
    plt.tight_layout()
    plt.savefig(out_dir / f"total_pq_and_bus{bus}_voltage_vs_time.png", dpi=300)
    plt.close(fig)

    fig, ax = plt.subplots(2, 1, sharex=True, figsize=(8.5, 6.5))
    if len(d_pq):
        x_pq, _ = _time_axis_seconds_or_hours(d_pq["t_granted"])
        ax[0].plot(x_pq, d_pq["Q_total"], label="Q_total (pu)")
        ax[0].set_ylabel("Total Load (pu)")
        ax[0].legend()
    ax[0].set_title("Total Load and Iteration vs Time")

    if len(by_t):
        x_iter, _ = _time_axis_seconds_or_hours(by_t["t_granted"])
        ax[1].plot(x_iter, by_t["iter"])
        ax[1].set_ylabel("Iteration at each time step")
    ax[1].set_xlabel(xlabel)

    plt.tight_layout()
    plt.savefig(out_dir / "total_pq_and_iteration_vs_time.png", dpi=300)
    plt.close(fig)

    d_event_v = _dropna_if_present(
        by_t,
        [
            "event_line_idx",
            "event_bus1",
            "event_bus2",
            "event_bus1_vmag",
            "event_bus2_vmag",
        ],
    )
    if len(d_event_v):
        event_line_idx = _first_valid_value(d_event_v, "event_line_idx")
        event_bus1 = int(_first_valid_value(d_event_v, "event_bus1"))
        event_bus2 = int(_first_valid_value(d_event_v, "event_bus2"))

        plt.figure(figsize=(8.5, 4.8))
        x_event_v, _ = _time_axis_seconds_or_hours(d_event_v["t_granted"])
        plt.plot(x_event_v, d_event_v["event_bus1_vmag"], label=f"Bus {event_bus1} |V|")
        plt.plot(x_event_v, d_event_v["event_bus2_vmag"], label=f"Bus {event_bus2} |V|")
        if "Vmag" in d_event_v.columns:
            plt.plot(
                x_event_v,
                d_event_v["Vmag"],
                linestyle="--",
                linewidth=1.5,
                label=f"Bus {bus} |V|",
            )
            _apply_zoom_ylim(
                plt.gca(),
                [
                    d_event_v["event_bus1_vmag"],
                    d_event_v["event_bus2_vmag"],
                    d_event_v["Vmag"],
                ],
                min_pad=5e-4,
            )
        else:
            _apply_zoom_ylim(
                plt.gca(),
                [d_event_v["event_bus1_vmag"], d_event_v["event_bus2_vmag"]],
                min_pad=5e-4,
            )
        plt.xlabel(xlabel)
        plt.ylabel("Voltage Magnitude |V| (pu)")
        plt.title(f"Voltages Near Tripped Line {event_line_idx}")
        plt.legend()
        plt.tight_layout()
        event_v_png = out_dir / "event_line_endpoint_voltages_vs_time.png"
        plt.savefig(event_v_png, dpi=300)
        plt.close()
        extra_pngs.append(event_v_png.name)

    d_event_ang = _dropna_if_present(
        by_t,
        [
            "event_line_idx",
            "event_bus1",
            "event_bus2",
            "event_bus1_vang_rad",
            "event_bus2_vang_rad",
            "event_bus_angle_diff_deg",
        ],
    )
    if len(d_event_ang):
        event_line_idx = _first_valid_value(d_event_ang, "event_line_idx")
        event_bus1 = int(_first_valid_value(d_event_ang, "event_bus1"))
        event_bus2 = int(_first_valid_value(d_event_ang, "event_bus2"))

        fig, ax = plt.subplots(2, 1, sharex=True, figsize=(8.5, 6.5))
        x_event_ang, _ = _time_axis_seconds_or_hours(d_event_ang["t_granted"])
        bus1_ang_deg = d_event_ang["event_bus1_vang_rad"] * 180.0 / math.pi
        bus2_ang_deg = d_event_ang["event_bus2_vang_rad"] * 180.0 / math.pi

        ax[0].plot(x_event_ang, bus1_ang_deg, label=f"Bus {event_bus1} angle")
        ax[0].plot(x_event_ang, bus2_ang_deg, label=f"Bus {event_bus2} angle")
        if "Vang_rad" in d_event_ang.columns:
            ax[0].plot(
                x_event_ang,
                d_event_ang["Vang_rad"] * 180.0 / math.pi,
                linestyle="--",
                linewidth=1.5,
                label=f"Bus {bus} angle",
            )
        _apply_zoom_ylim(ax[0], [bus1_ang_deg, bus2_ang_deg], min_pad=0.05)
        ax[0].set_ylabel("Angle (deg)")
        ax[0].set_title(f"Angles Near Tripped Line {event_line_idx}")
        ax[0].legend()
        ax[0].grid(True)

        ax[1].plot(
            x_event_ang,
            d_event_ang["event_bus_angle_diff_deg"],
            label=f"{event_bus1}-{event_bus2} angle diff",
        )
        _apply_zoom_ylim(ax[1], [d_event_ang["event_bus_angle_diff_deg"]], min_pad=0.05)
        ax[1].set_ylabel("Angle diff (deg)")
        ax[1].set_xlabel(xlabel)
        ax[1].legend()
        ax[1].grid(True)

        plt.tight_layout()
        event_ang_png = out_dir / "event_line_endpoint_angles_vs_time.png"
        plt.savefig(event_ang_png, dpi=300)
        plt.close(fig)
        extra_pngs.append(event_ang_png.name)

    d_delta = _dropna_if_present(
        by_t, ["delta_min_deg", "delta_max_deg", "delta_spread_deg"]
    )
    if len(d_delta):
        fig, ax = plt.subplots(2, 1, sharex=True, figsize=(8.5, 6.5))
        x_delta, _ = _time_axis_seconds_or_hours(d_delta["t_granted"])
        ax[0].plot(x_delta, d_delta["delta_min_deg"], label="delta_min (deg)")
        ax[0].plot(x_delta, d_delta["delta_max_deg"], label="delta_max (deg)")
        ax[0].set_ylabel("Rotor angle (deg)")
        ax[0].set_title("GENROU rotor angle envelope")
        ax[0].legend()
        ax[0].grid(True)

        ax[1].plot(x_delta, d_delta["delta_spread_deg"], label="delta_spread (deg)")
        ax[1].set_ylabel("Spread (deg)")
        ax[1].set_xlabel(xlabel)
        ax[1].legend()
        ax[1].grid(True)

        plt.tight_layout()
        rotor_png = out_dir / "genrou_rotor_angles_vs_time.png"
        plt.savefig(rotor_png, dpi=300)
        plt.close(fig)
        extra_pngs.append(rotor_png.name)

    d_omega = _dropna_if_present(
        by_t, ["omega_min_pu", "omega_max_pu", "omega_max_dev"]
    )
    if len(d_omega):
        fig, ax = plt.subplots(2, 1, sharex=True, figsize=(8.5, 6.5))
        x_omega, _ = _time_axis_seconds_or_hours(d_omega["t_granted"])
        ax[0].plot(x_omega, d_omega["omega_min_pu"], label="omega_min (pu)")
        ax[0].plot(x_omega, d_omega["omega_max_pu"], label="omega_max (pu)")
        ax[0].axhline(1.0, color="k", linestyle="--", linewidth=1.0, label="sync speed")
        ax[0].set_ylabel("Speed (pu)")
        ax[0].set_title("GENROU speed envelope")
        ax[0].legend()
        ax[0].grid(True)

        ax[1].plot(x_omega, d_omega["omega_max_dev"], label="max |omega-1|")
        ax[1].set_ylabel("Speed deviation (pu)")
        ax[1].set_xlabel(xlabel)
        ax[1].legend()
        ax[1].grid(True)

        plt.tight_layout()
        omega_png = out_dir / "genrou_speed_vs_time.png"
        plt.savefig(omega_png, dpi=300)
        plt.close(fig)
        extra_pngs.append(omega_png.name)

    d_vf = _dropna_if_present(by_t, ["vf_min_pu", "vf_max_pu"])
    if len(d_vf):
        plt.figure()
        x_vf, _ = _time_axis_seconds_or_hours(d_vf["t_granted"])
        plt.plot(x_vf, d_vf["vf_min_pu"], label="vf_min (pu)")
        plt.plot(x_vf, d_vf["vf_max_pu"], label="vf_max (pu)")
        plt.xlabel(xlabel)
        plt.ylabel("Field voltage (pu)")
        plt.title("GENROU field voltage envelope vs Time")
        plt.legend()
        plt.tight_layout()
        vf_png = out_dir / "genrou_field_voltage_vs_time.png"
        plt.savefig(vf_png, dpi=300)
        plt.close()
        extra_pngs.append(vf_png.name)

    d_critical_bus = _dropna_if_present(
        by_t,
        [
            "delta_min_bus",
            "delta_max_bus",
            "omega_max_bus",
            "vf_max_bus",
        ],
    )
    if len(d_critical_bus):
        plt.figure(figsize=(8.5, 4.8))
        x_critical, _ = _time_axis_seconds_or_hours(d_critical_bus["t_granted"])
        plt.step(x_critical, d_critical_bus["delta_min_bus"], where="post", label="delta_min bus")
        plt.step(x_critical, d_critical_bus["delta_max_bus"], where="post", label="delta_max bus")
        plt.step(x_critical, d_critical_bus["omega_max_bus"], where="post", label="omega_max bus")
        plt.step(x_critical, d_critical_bus["vf_max_bus"], where="post", label="vf_max bus")
        _apply_zoom_ylim(
            plt.gca(),
            [
                d_critical_bus["delta_min_bus"],
                d_critical_bus["delta_max_bus"],
                d_critical_bus["omega_max_bus"],
                d_critical_bus["vf_max_bus"],
            ],
            min_pad=1.0,
        )
        plt.xlabel(xlabel)
        plt.ylabel("GENROU bus number")
        plt.title("Critical GENROU buses vs Time")
        plt.legend()
        plt.tight_layout()
        critical_bus_png = out_dir / "genrou_critical_buses_vs_time.png"
        plt.savefig(critical_bus_png, dpi=300)
        plt.close()
        extra_pngs.append(critical_bus_png.name)

    if {
        "delta_min_idx",
        "delta_max_idx",
        "omega_max_idx",
        "vf_max_idx",
    }.issubset(by_t.columns) and len(by_t):
        final_row = by_t.iloc[-1]
        print(
            "[INFO] Final critical GENROUs: "
            f"delta_min={_format_genrou_identity(final_row, 'delta_min_idx', 'delta_min_bus')}, "
            f"delta_max={_format_genrou_identity(final_row, 'delta_max_idx', 'delta_max_bus')}, "
            f"omega_max={_format_genrou_identity(final_row, 'omega_max_idx', 'omega_max_bus')}, "
            f"vf_max={_format_genrou_identity(final_row, 'vf_max_idx', 'vf_max_bus')}"
        )

    if {
        "omega_max_idx",
        "omega_max_bus",
        "omega_max_dev",
    }.issubset(by_t.columns):
        d_peak_omega = _dropna_if_present(by_t, ["omega_max_idx", "omega_max_bus", "omega_max_dev"])
        if len(d_peak_omega):
            peak_omega_row = d_peak_omega.loc[d_peak_omega["omega_max_dev"].idxmax()]
            print(
                "[INFO] Peak omega deviation: "
                f"{peak_omega_row['omega_max_dev']:.6f} pu at t={peak_omega_row['t_granted']:.3f}s "
                f"by {_format_genrou_identity(peak_omega_row, 'omega_max_idx', 'omega_max_bus')}"
            )

    if {
        "vf_max_idx",
        "vf_max_bus",
        "vf_max_pu",
    }.issubset(by_t.columns):
        d_peak_vf = _dropna_if_present(by_t, ["vf_max_idx", "vf_max_bus", "vf_max_pu"])
        if len(d_peak_vf):
            peak_vf_row = d_peak_vf.loc[d_peak_vf["vf_max_pu"].idxmax()]
            print(
                "[INFO] Peak field voltage: "
                f"{peak_vf_row['vf_max_pu']:.6f} pu at t={peak_vf_row['t_granted']:.3f}s "
                f"by {_format_genrou_identity(peak_vf_row, 'vf_max_idx', 'vf_max_bus')}"
            )

    if {
        "delta_min_idx",
        "delta_min_bus",
        "delta_max_idx",
        "delta_max_bus",
        "delta_spread_deg",
    }.issubset(by_t.columns):
        d_peak_delta = _dropna_if_present(
            by_t,
            [
                "delta_min_idx",
                "delta_min_bus",
                "delta_max_idx",
                "delta_max_bus",
                "delta_spread_deg",
            ],
        )
        if len(d_peak_delta):
            peak_delta_row = d_peak_delta.loc[d_peak_delta["delta_spread_deg"].idxmax()]
            print(
                "[INFO] Peak rotor-angle spread: "
                f"{peak_delta_row['delta_spread_deg']:.6f} deg at t={peak_delta_row['t_granted']:.3f}s "
                f"between {_format_genrou_identity(peak_delta_row, 'delta_min_idx', 'delta_min_bus')} "
                f"and {_format_genrou_identity(peak_delta_row, 'delta_max_idx', 'delta_max_bus')}"
            )

    csv_path = out_dir / "parsed_transmission.csv"
    df.to_csv(csv_path, index=False)
    print(f"[OK] Saved CSV: {csv_path}")
    print(f"[OK] Saved PNGs to: {out_dir}")
    print("     - total_pq_vs_time.png")
    print(f"     - bus{bus}_voltage_vs_time.png")
    print(f"     - total_pq_and_bus{bus}_voltage_vs_time.png")
    print("     - total_pq_and_iteration_vs_time.png")
    for png_name in extra_pngs:
        print(f"     - {png_name}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=str, default="transmission.log", help="Path to transmission.log")
    parser.add_argument("--out", type=str, default=None, help="Output folder (default: same folder as log)")
    parser.add_argument("--bus", type=int, default=2, help="Bus index for |V| (default: 2)")
    args = parser.parse_args()

    log_path = Path(args.log).expanduser().resolve()
    if not log_path.exists():
        raise FileNotFoundError(f"Log not found: {log_path}")

    out_dir = Path(args.out).expanduser().resolve() if args.out else log_path.parent

    print(f"[INFO] Log: {log_path}")
    print(f"[INFO] Out: {out_dir}")
    print(f"[INFO] Bus: {args.bus}")

    df = load_transmission_plot_data(log_path, bus=args.bus)
    make_plots(df, out_dir, bus=args.bus)


if __name__ == "__main__":
    main()
