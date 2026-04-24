#!/usr/bin/env python3
"""
Parse feeder logs and plot the distribution-side bus voltage tracked by Distribution.py.

Usage:
  python3 plot_distribution_from_logs.py --log feeder_1.log
  python3 plot_distribution_from_logs.py --log feeder_1.log --out plots/
"""

import argparse
import math
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd

from fidvr_alerts import (
    ALERT_COLORS,
    DEFAULT_OVERVOLTAGE_LOOKAHEAD_S,
    DEFAULT_OVERVOLTAGE_ALERT_PU,
    DEFAULT_STALL_ALERT_VOLTAGE_PU,
    alert_summary_lines,
    detect_fidvr_alerts,
)


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
    r"(?:\s+Vpos=(?P<vpos>[0-9.+\-eE]+|nan)\s+pu)?"
)
ALERT_RE = re.compile(
    r"AlertSignal=(?P<alert_signal>[A-Za-z_]+)\s+"
    r"AlertBus=(?P<alert_bus>\S+)\s+"
    r"AlertV=(?P<alert_v>[0-9.+\-eE]+|nan)\s+pu\s+"
    r"AlertVpos=(?P<alert_vpos>[0-9.+\-eE]+|nan)\s+pu\s+"
    r"AlertVavg=(?P<alert_vavg>[0-9.+\-eE]+|nan)\s+pu"
)
FIDVR_RE = re.compile(
    r"FIDVR=(?P<fidvr_stage>[A-Z_]+)\s+"
    r"TxV=(?P<tx_v_pu>[0-9.+\-eE]+)\s+"
    r"MotorP=(?P<motor_p_scale>[0-9.+\-eE]+)\s+"
    r"MotorQ=(?P<motor_q_scale>[0-9.+\-eE]+)\s+"
    r"Caps=(?P<caps_on>on|off)\s+"
    r"(?:CapFrac=(?P<cap_fraction>[0-9.+\-eE]+)\s+)?"
    r"Tap=(?P<tap_pu>[0-9.+\-eE]+)\s+"
    r"Restore=(?P<restore_frac>[0-9.+\-eE]+)"
)
MOTOR_DIAG_RE = re.compile(
    r"SlipAvg=(?P<motor_slip_avg>[0-9.+\-eE]+|nan)\s+"
    r"SlipMax=(?P<motor_slip_max>[0-9.+\-eE]+|nan)\s+"
    r"MotorPF=(?P<motor_pf_avg>[0-9.+\-eE]+|nan)"
)
MOTOR_STATE_RE = re.compile(
    r"Running=(?P<motor_running_groups>\d+)\s+"
    r"Stalled=(?P<motor_stalled_groups>\d+)\s+"
    r"Tripped=(?P<motor_tripped_groups>\d+)\s+"
    r"Restoring=(?P<motor_restoring_groups>\d+)\s+"
    r"Dyn=(?P<dynamics_enabled>on|off)"
)
REG_TAP_RE = re.compile(r"Tap(?P<name>[A-Za-z0-9_]+)=(?P<value>[0-9.+\-eE]+)")

FIDVR_STAGE_LABELS = {
    "FAULT_ACTIVE": "Fault",
    "STALLED_MOTORS": "Stalled Motors",
    "OVERSHOOT": "Overshoot",
    "CAPS_OFF": "Caps Off",
    "LOAD_RESTORATION": "Load Restoration",
}

FIDVR_STAGE_COLORS = {
    "FAULT_ACTIVE": "#d73027",
    "STALLED_MOTORS": "#fc8d59",
    "OVERSHOOT": "#91bfdb",
    "CAPS_OFF": "#4575b4",
    "LOAD_RESTORATION": "#74add1",
}

ALERT_TEXT_Y = {
    "Alert.1": 0.06,
    "Alert.2": 0.13,
    "Alert.3": 0.20,
}


def _float_or_nan(value: str) -> float:
    return float("nan") if value.lower() == "nan" else float(value)


def _time_axis_seconds_or_hours(t_seconds: pd.Series):
    if t_seconds.nunique() >= 2 and (t_seconds.max() - t_seconds.min()) >= 3600:
        return t_seconds / 3600.0, "Time (hours)"
    return t_seconds, "Time (s)"


def _alert_signal_label(by_t: pd.DataFrame, dist_metric_label: str) -> str:
    if "alert_signal" not in by_t.columns or by_t["alert_signal"].dropna().empty:
        return dist_metric_label

    alert_signal = str(by_t["alert_signal"].dropna().iloc[-1])
    if alert_signal == "source":
        return "Interface source |V|"

    alert_bus = (
        str(by_t["alert_bus"].dropna().iloc[-1])
        if "alert_bus" in by_t.columns and not by_t["alert_bus"].dropna().empty
        else str(by_t["dist_bus"].dropna().iloc[-1])
    )
    if alert_bus == str(by_t["dist_bus"].dropna().iloc[-1]):
        return dist_metric_label
    if "." in alert_bus:
        return f"{alert_bus} |V|"
    if "alert_vpos_pu" in by_t.columns and by_t["alert_vpos_pu"].notna().any():
        return f"{alert_bus} |V1|"
    return f"{alert_bus} avg |V|"


def _set_voltage_limits(ax, series_list):
    values = pd.concat(series_list, axis=0).dropna()
    if values.empty:
        return

    vmin = values.min()
    vmax = values.max()
    pad = max(0.002, 0.1 * max(vmax - vmin, 0.01))
    ax.set_ylim(vmin - pad, vmax + pad)


def _load_disturbance_intervals(log_path: Path):
    csv_path = log_path.parent / "transmission_timeseries.csv"
    if not csv_path.exists():
        return None, []

    df = pd.read_csv(csv_path)
    status_column = None
    line_idx_column = None
    if {"t_granted", "postfault_line_status"}.issubset(df.columns):
        status_column = "postfault_line_status"
        line_idx_column = "postfault_line_idx"
    elif {"t_granted", "event_line_status"}.issubset(df.columns):
        status_column = "event_line_status"
        line_idx_column = "event_line_idx"
    else:
        return None, []

    by_t = (
        df.dropna(subset=["t_granted", status_column])
        .sort_values("t_granted")
        .groupby("t_granted", as_index=False)
        .last()
    )
    if by_t.empty:
        return None, []

    line_idx_series = by_t.get(line_idx_column)
    line_idx = None
    if line_idx_series is not None:
        valid_line_idx = line_idx_series.dropna()
        if not valid_line_idx.empty:
            line_idx = str(valid_line_idx.iloc[0])

    status_series = pd.to_numeric(by_t[status_column], errors="coerce").ffill()
    transitions = status_series.ne(status_series.shift())

    intervals = []
    outage_start = None
    for row, status_value, changed in zip(
        by_t.itertuples(index=False), status_series, transitions
    ):
        if not changed:
            continue
        status_int = int(round(status_value))
        if status_int == 0 and outage_start is None:
            outage_start = float(row.t_granted)
        elif status_int != 0 and outage_start is not None:
            intervals.append((outage_start, float(row.t_granted)))
            outage_start = None

    if outage_start is not None:
        intervals.append((outage_start, float(by_t["t_granted"].iloc[-1])))

    return line_idx, intervals


def _load_fault_intervals(log_path: Path):
    csv_path = log_path.parent / "transmission_timeseries.csv"
    if not csv_path.exists():
        return None, []

    df = pd.read_csv(csv_path)
    if not {"t_granted", "fault_active"}.issubset(df.columns):
        return None, []

    by_t = (
        df.dropna(subset=["t_granted", "fault_active"])
        .sort_values("t_granted")
        .groupby("t_granted", as_index=False)
        .last()
    )
    if by_t.empty:
        return None, []

    fault_bus = None
    if "fault_bus" in by_t.columns:
        valid_fault_bus = by_t["fault_bus"].dropna()
        if not valid_fault_bus.empty:
            fault_bus = valid_fault_bus.iloc[0]

    status_series = pd.to_numeric(by_t["fault_active"], errors="coerce").ffill().fillna(0.0)
    active_series = status_series >= 0.5

    intervals = []
    active_start = None
    for row, active in zip(by_t.itertuples(index=False), active_series):
        if active and active_start is None:
            active_start = float(row.t_granted)
        elif not active and active_start is not None:
            intervals.append((active_start, float(row.t_granted)))
            active_start = None

    if active_start is not None:
        intervals.append((active_start, float(by_t["t_granted"].iloc[-1])))

    return fault_bus, intervals


def _extract_fidvr_stage_intervals(by_t: pd.DataFrame):
    if "fidvr_stage" not in by_t.columns:
        return []

    neutral_stages = {"DISABLED", "BASELINE", "RECOVERED"}
    stage_series = by_t["fidvr_stage"].fillna("DISABLED")
    if stage_series.empty:
        return []

    intervals = []
    current_stage = stage_series.iloc[0]
    current_start = float(by_t["t_granted"].iloc[0])

    for idx in range(1, len(by_t)):
        next_stage = stage_series.iloc[idx]
        if next_stage == current_stage:
            continue
        if current_stage not in neutral_stages:
            intervals.append(
                (current_start, float(by_t["t_granted"].iloc[idx]), current_stage)
            )
        current_stage = next_stage
        current_start = float(by_t["t_granted"].iloc[idx])

    if current_stage not in neutral_stages:
        intervals.append(
            (current_start, float(by_t["t_granted"].iloc[-1]), current_stage)
        )

    return intervals


def _time_value_in_plot_units(t_seconds: float, xlabel: str) -> float:
    if "hours" in xlabel.lower():
        return t_seconds / 3600.0
    return t_seconds


def _add_disturbance_overlays(ax, disturbance_intervals, xlabel: str, line_idx=None):
    if isinstance(disturbance_intervals, dict):
        fault_intervals = disturbance_intervals.get("fault_intervals", [])
        fault_bus = disturbance_intervals.get("fault_bus")
        for start, end in fault_intervals:
            x_start = _time_value_in_plot_units(start, xlabel)
            x_end = _time_value_in_plot_units(end, xlabel)
            if fault_bus is not None and not pd.isna(fault_bus):
                label = f"Fault @ bus {int(fault_bus)}"
            else:
                label = "Fault"
            ax.axvspan(x_start, x_end, color="tab:red", alpha=0.12, label=label)
            fault_bus = None
        if line_idx is None:
            line_idx = disturbance_intervals.get("line_idx")
        disturbance_intervals = disturbance_intervals.get("line_intervals", [])
    for start, end in disturbance_intervals:
        x_start = _time_value_in_plot_units(start, xlabel)
        x_end = _time_value_in_plot_units(end, xlabel)
        label = None
        if line_idx:
            label = f"{line_idx} outage"
            line_idx = None
        ax.axvspan(x_start, x_end, color="tab:red", alpha=0.12, label=label)


def _add_fidvr_stage_overlays(ax, stage_intervals, xlabel: str):
    seen = set()
    for start, end, stage in stage_intervals:
        x_start = _time_value_in_plot_units(start, xlabel)
        x_end = _time_value_in_plot_units(end, xlabel)
        label = None
        if stage not in seen:
            label = FIDVR_STAGE_LABELS.get(stage, stage.replace("_", " ").title())
            seen.add(stage)
        ax.axvspan(
            x_start,
            x_end,
            color=FIDVR_STAGE_COLORS.get(stage, "#cccccc"),
            alpha=0.08,
            label=label,
        )


def _annotate_fidvr_stages(ax, stage_intervals, xlabel: str):
    for start, end, stage in stage_intervals:
        x_mid = _time_value_in_plot_units(0.5 * (start + end), xlabel)
        ax.text(
            x_mid,
            0.98,
            FIDVR_STAGE_LABELS.get(stage, stage.replace("_", " ").title()),
            transform=ax.get_xaxis_transform(),
            ha="center",
            va="top",
            fontsize=8.5,
            color=FIDVR_STAGE_COLORS.get(stage, "black"),
        )


def _add_alert_threshold_lines(ax, reference_voltage_pu: float):
    stall_threshold_pu = reference_voltage_pu * DEFAULT_STALL_ALERT_VOLTAGE_PU
    overvoltage_threshold_pu = reference_voltage_pu * DEFAULT_OVERVOLTAGE_ALERT_PU
    ax.axhline(
        stall_threshold_pu,
        color=ALERT_COLORS["Alert.2"],
        linewidth=1.0,
        linestyle=":",
        label=f"Alert.2 threshold ({stall_threshold_pu:.3f} pu)",
    )
    ax.axhline(
        overvoltage_threshold_pu,
        color=ALERT_COLORS["Alert.3"],
        linewidth=1.0,
        linestyle=":",
        label=f"Alert.3 threshold ({overvoltage_threshold_pu:.3f} pu)",
    )


def _add_alert_deviation_lines(ax, reference_voltage_pu: float):
    stall_threshold_pct = (reference_voltage_pu * DEFAULT_STALL_ALERT_VOLTAGE_PU - 1.0) * 100.0
    overvoltage_threshold_pct = (
        reference_voltage_pu * DEFAULT_OVERVOLTAGE_ALERT_PU - 1.0
    ) * 100.0
    ax.axhline(
        stall_threshold_pct,
        color=ALERT_COLORS["Alert.2"],
        linewidth=1.0,
        linestyle=":",
        label=f"Alert.2 threshold ({stall_threshold_pct:.1f}%)",
    )
    ax.axhline(
        overvoltage_threshold_pct,
        color=ALERT_COLORS["Alert.3"],
        linewidth=1.0,
        linestyle=":",
        label=f"Alert.3 threshold ({overvoltage_threshold_pct:.1f}%)",
    )


def _add_alert_overlays(ax, alerts: pd.DataFrame, xlabel: str):
    seen = set()
    for row in alerts.itertuples(index=False):
        if not bool(row.triggered):
            continue
        x_pos = _time_value_in_plot_units(float(row.trigger_time_s), xlabel)
        label = row.alert_id if row.alert_id not in seen else None
        seen.add(row.alert_id)
        ax.axvline(
            x_pos,
            color=ALERT_COLORS.get(row.alert_id, "black"),
            linewidth=1.3,
            linestyle="--",
            alpha=0.9,
            label=label,
        )
        ax.text(
            x_pos,
            ALERT_TEXT_Y.get(row.alert_id, 0.06),
            row.alert_id,
            transform=ax.get_xaxis_transform(),
            rotation=90,
            ha="right",
            va="bottom",
            fontsize=8.5,
            color=ALERT_COLORS.get(row.alert_id, "black"),
        )


def _add_alert_window_overlay(ax, alerts: pd.DataFrame, xlabel: str):
    stall_rows = alerts.loc[alerts["alert_id"] == "Alert.2"]
    if stall_rows.empty or not bool(stall_rows.iloc[0]["triggered"]):
        return

    stall_row = stall_rows.iloc[0]
    window_start = _time_value_in_plot_units(float(stall_row["trigger_time_s"]), xlabel)
    window_end = _time_value_in_plot_units(
        float(stall_row["trigger_time_s"]) + DEFAULT_OVERVOLTAGE_LOOKAHEAD_S,
        xlabel,
    )
    ax.axvspan(
        window_start,
        window_end,
        color=ALERT_COLORS["Alert.3"],
        alpha=0.04,
        label="Alert.3 lookahead",
    )

    overvoltage_rows = alerts.loc[alerts["alert_id"] == "Alert.3"]
    if overvoltage_rows.empty or not bool(overvoltage_rows.iloc[0]["triggered"]):
        return

    overvoltage_row = overvoltage_rows.iloc[0]
    trigger_start = _time_value_in_plot_units(float(overvoltage_row["start_time_s"]), xlabel)
    trigger_end = _time_value_in_plot_units(float(overvoltage_row["end_time_s"]), xlabel)
    ax.axvspan(
        trigger_start,
        trigger_end,
        color=ALERT_COLORS["Alert.3"],
        alpha=0.12,
        label="Alert.3 active",
    )


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
                    "vpos_pu": _float_or_nan(dist.group("vpos"))
                    if dist.group("vpos") is not None
                    else math.nan,
                }
            )

        alert = ALERT_RE.search(line)
        if alert:
            row.update(
                {
                    "alert_signal": alert.group("alert_signal"),
                    "alert_bus": alert.group("alert_bus"),
                    "alert_v_pu": _float_or_nan(alert.group("alert_v")),
                    "alert_vpos_pu": _float_or_nan(alert.group("alert_vpos")),
                    "alert_vavg_pu": _float_or_nan(alert.group("alert_vavg")),
                }
            )

        fidvr = FIDVR_RE.search(line)
        if fidvr:
            row.update(
                {
                    "fidvr_stage": fidvr.group("fidvr_stage"),
                    "tx_v_pu": float(fidvr.group("tx_v_pu")),
                    "motor_p_scale": float(fidvr.group("motor_p_scale")),
                    "motor_q_scale": float(fidvr.group("motor_q_scale")),
                    "caps_on": fidvr.group("caps_on") == "on",
                    "cap_fraction": float(fidvr.group("cap_fraction"))
                    if fidvr.group("cap_fraction") is not None
                    else (1.0 if fidvr.group("caps_on") == "on" else 0.0),
                    "tap_pu": float(fidvr.group("tap_pu")),
                    "restore_frac": float(fidvr.group("restore_frac")),
                }
            )

        motor_diag = MOTOR_DIAG_RE.search(line)
        if motor_diag:
            row.update(
                {
                    "motor_slip_avg": _float_or_nan(motor_diag.group("motor_slip_avg")),
                    "motor_slip_max": _float_or_nan(motor_diag.group("motor_slip_max")),
                    "motor_pf_avg": _float_or_nan(motor_diag.group("motor_pf_avg")),
                }
            )

        motor_state = MOTOR_STATE_RE.search(line)
        if motor_state:
            row.update(
                {
                    "motor_running_groups": int(motor_state.group("motor_running_groups")),
                    "motor_stalled_groups": int(motor_state.group("motor_stalled_groups")),
                    "motor_tripped_groups": int(motor_state.group("motor_tripped_groups")),
                    "motor_restoring_groups": int(motor_state.group("motor_restoring_groups")),
                    "dynamics_enabled": motor_state.group("dynamics_enabled") == "on",
                }
            )

        for tap_match in REG_TAP_RE.finditer(line):
            tap_name = tap_match.group("name")
            tap_key = f"tap_{tap_name.lower()}"
            row[tap_key] = float(tap_match.group("value"))

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


def make_plots(df: pd.DataFrame, out_dir: Path, log_stem: str, log_path: Path):
    out_dir.mkdir(parents=True, exist_ok=True)

    # Prefer rows where the feeder actually received a new transmission-voltage
    # update. If a time has no updated row, fall back to the settled NEXT_STEP
    # row and then to the latest row for that granted time.
    by_t = (
        df.assign(
            update_rank=df["vupdate"].astype(int),
            state_rank=(df["state"] == "NEXT_STEP").astype(int),
        )
        .sort_values(["t_granted", "update_rank", "state_rank", "iter"])
        .groupby("t_granted", as_index=False)
        .last()
        .sort_values("t_granted")
        .drop(columns=["update_rank", "state_rank"])
    )
    x, xlabel = _time_axis_seconds_or_hours(by_t["t_granted"])
    dist_bus = by_t["dist_bus"].dropna().iloc[-1]
    feeder = int(by_t["feeder"].iloc[-1])
    disturbance_line_idx, line_outage_intervals = _load_disturbance_intervals(log_path)
    fault_bus, fault_intervals = _load_fault_intervals(log_path)
    disturbance_intervals = {
        "line_idx": disturbance_line_idx,
        "line_intervals": line_outage_intervals,
        "fault_bus": fault_bus,
        "fault_intervals": fault_intervals,
    }
    fidvr_stage_intervals = _extract_fidvr_stage_intervals(by_t)
    has_vpos = "vpos_pu" in by_t.columns and by_t["vpos_pu"].notna().any()
    dist_metric = "vpos_pu" if has_vpos else "vavg_pu"
    dist_metric_label = f"{dist_bus} |V1|" if has_vpos else f"{dist_bus} avg |V|"
    alert_signal_label = dist_metric_label
    if "alert_v_pu" in by_t.columns and by_t["alert_v_pu"].notna().any():
        alert_signal = by_t["alert_v_pu"].ffill().bfill()
        alert_signal_label = _alert_signal_label(by_t, dist_metric_label)
    else:
        alert_signal = by_t[dist_metric].fillna(by_t["vavg_pu"])
    reference_voltage_pu = float(alert_signal.dropna().iloc[0])
    alerts = detect_fidvr_alerts(
        by_t["t_granted"],
        alert_signal,
        reference_voltage_pu=reference_voltage_pu,
    )
    if fault_intervals:
        intervals_str = ", ".join(
            f"[{start:.3f}, {end:.3f}] s" for start, end in fault_intervals
        )
        print(
            f"[INFO] Fault intervals for bus {fault_bus if fault_bus is not None else 'n/a'}: "
            f"{intervals_str}"
        )
    if line_outage_intervals:
        intervals_str = ", ".join(
            f"[{start:.3f}, {end:.3f}] s" for start, end in line_outage_intervals
        )
        print(
            f"[INFO] Post-fault line intervals for {disturbance_line_idx or 'monitored line'}: "
            f"{intervals_str}"
        )

    fig, axes = plt.subplots(2, 1, sharex=True, figsize=(9, 7))

    axes[0].plot(x, by_t["source_v_pu"], label="Interface source |V|", linewidth=1.8)
    axes[0].plot(
        x,
        by_t[dist_metric],
        label=dist_metric_label,
        linewidth=1.8,
        linestyle="--",
    )
    if alert_signal_label not in {"Interface source |V|", dist_metric_label}:
        axes[0].plot(
            x,
            alert_signal,
            label=f"Alert signal ({alert_signal_label})",
            linewidth=1.4,
            linestyle="-.",
            color="black",
        )
    if has_vpos:
        axes[0].plot(
            x,
            by_t["vavg_pu"],
            label=f"{dist_bus} avg |V|",
            linewidth=1.2,
            linestyle=":",
    )
    _add_alert_threshold_lines(axes[0], reference_voltage_pu)
    _add_alert_window_overlay(axes[0], alerts, xlabel)
    _add_alert_overlays(axes[0], alerts, xlabel)
    _add_fidvr_stage_overlays(axes[0], fidvr_stage_intervals, xlabel)
    _add_disturbance_overlays(axes[0], disturbance_intervals, xlabel, line_idx=disturbance_line_idx)
    axes[0].set_ylabel("Voltage (pu)")
    axes[0].set_title(
        f"Feeder {feeder}: Interface vs distribution-side voltage "
        f"(alerts on {alert_signal_label})"
    )
    axes[0].grid(True)
    voltage_series = [by_t["source_v_pu"], by_t[dist_metric]]
    if has_vpos:
        voltage_series.append(by_t["vavg_pu"])
    _set_voltage_limits(axes[0], voltage_series)
    axes[0].legend()

    axes[1].plot(x, by_t["va_pu"], label="Phase A", linewidth=1.6)
    axes[1].plot(x, by_t["vb_pu"], label="Phase B", linewidth=1.6)
    axes[1].plot(x, by_t["vc_pu"], label="Phase C", linewidth=1.6)
    axes[1].plot(x, by_t["vavg_pu"], label="Average", linestyle="--", linewidth=1.8)
    if has_vpos:
        axes[1].plot(x, by_t["vpos_pu"], label="Positive sequence", linestyle="-.", linewidth=1.8)
    _add_alert_threshold_lines(axes[1], reference_voltage_pu)
    _add_alert_window_overlay(axes[1], alerts, xlabel)
    _add_alert_overlays(axes[1], alerts, xlabel)
    _add_fidvr_stage_overlays(axes[1], fidvr_stage_intervals, xlabel)
    _add_disturbance_overlays(axes[1], disturbance_intervals, xlabel)
    axes[1].set_ylabel("Voltage (pu)")
    axes[1].set_xlabel(xlabel)
    axes[1].set_title(f"Distribution bus {dist_bus} voltage by phase")
    _set_voltage_limits(
        axes[1],
        [by_t["va_pu"], by_t["vb_pu"], by_t["vc_pu"], by_t["vavg_pu"]]
        + ([by_t["vpos_pu"]] if has_vpos else []),
    )
    axes[1].grid(True)
    axes[1].legend()


    fig.suptitle("OpenDSS Distribution-Side Voltage vs Time", fontsize=14)
    plt.tight_layout()

    plot_path = out_dir / f"{log_stem}_distribution_voltage_vs_time.png"
    fig.savefig(plot_path, dpi=300)
    plt.close(fig)

    # Save a transmission-style single-line voltage plot for the tracked bus.
    fig, ax = plt.subplots(figsize=(8.5, 4.5))
    ax.plot(x, by_t[dist_metric], color="tab:green", linewidth=2, label=dist_metric_label)
    if has_vpos:
        ax.plot(
            x,
            by_t["vavg_pu"],
            color="tab:olive",
            linewidth=1.3,
            linestyle=":",
            label=f"{dist_bus} avg |V|",
        )
    _add_alert_threshold_lines(ax, reference_voltage_pu)
    _add_alert_window_overlay(ax, alerts, xlabel)
    _add_alert_overlays(ax, alerts, xlabel)
    _add_fidvr_stage_overlays(ax, fidvr_stage_intervals, xlabel)
    _add_disturbance_overlays(ax, disturbance_intervals, xlabel, line_idx=disturbance_line_idx)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Voltage magnitude |V| (pu)")
    ax.set_title(f"Distribution bus {dist_bus} voltage")
    ax.grid(True)
    ax.legend()
    _set_voltage_limits(ax, [by_t[dist_metric]] + ([by_t["vavg_pu"]] if has_vpos else []))

    single_plot_path = out_dir / f"{log_stem}_distribution_bus_voltage_vs_time.png"
    fig.tight_layout()
    fig.savefig(single_plot_path, dpi=300)
    plt.close(fig)

    if {
        "fidvr_stage",
        "motor_p_scale",
        "motor_q_scale",
        "caps_on",
        "cap_fraction",
        "tap_pu",
        "restore_frac",
    }.issubset(by_t.columns):
        fig, axes = plt.subplots(2, 1, sharex=True, figsize=(10, 7.5))

        source_dev_pct = (by_t["source_v_pu"] - 1.0) * 100.0
        dist_dev_pct = (by_t[dist_metric] - 1.0) * 100.0
        axes[0].plot(x, source_dev_pct, linewidth=1.7, label="Interface source")
        axes[0].plot(
            x,
            dist_dev_pct,
            linewidth=2.0,
            linestyle="--",
            label=dist_metric_label,
        )
        if has_vpos:
            axes[0].plot(
                x,
                (by_t["vavg_pu"] - 1.0) * 100.0,
                linewidth=1.2,
                linestyle=":",
                label=f"{dist_bus} avg |V|",
            )
        axes[0].axhline(0.0, color="0.35", linewidth=1.0, linestyle=":")
        _add_alert_deviation_lines(axes[0], reference_voltage_pu)
        _add_alert_window_overlay(axes[0], alerts, xlabel)
        _add_alert_overlays(axes[0], alerts, xlabel)
        _add_fidvr_stage_overlays(axes[0], fidvr_stage_intervals, xlabel)
        _add_disturbance_overlays(
            axes[0], disturbance_intervals, xlabel, line_idx=disturbance_line_idx
        )
        _annotate_fidvr_stages(axes[0], fidvr_stage_intervals, xlabel)
        axes[0].set_ylabel("Voltage deviation (%)")
        axes[0].set_title(
            f"Feeder {feeder}: staged FIDVR trajectory at distribution bus {dist_bus}"
        )
        axes[0].grid(True)
        axes[0].legend()

        axes[1].plot(x, by_t["motor_p_scale"], label="Motor P scale", linewidth=1.7)
        axes[1].plot(x, by_t["motor_q_scale"], label="Motor Q scale", linewidth=1.7)
        axes[1].plot(x, by_t["restore_frac"], label="Restore fraction", linewidth=1.7)
        if {"motor_slip_avg", "motor_slip_max"}.issubset(by_t.columns):
            axes[1].plot(
                x,
                by_t["motor_slip_avg"],
                label="Average slip",
                linewidth=1.6,
            )
            axes[1].plot(
                x,
                by_t["motor_slip_max"],
                label="Max slip",
                linewidth=1.6,
                linestyle="--",
            )
        axes[1].plot(
            x,
            by_t["cap_fraction"],
            label="Capacitor fraction",
            linewidth=1.5,
        )
        _add_fidvr_stage_overlays(axes[1], fidvr_stage_intervals, xlabel)
        _add_disturbance_overlays(axes[1], disturbance_intervals, xlabel)
        axes[1].set_ylabel("Motor / cap state")
        axes[1].set_xlabel(xlabel)
        axes[1].grid(True)

        tap_ax = axes[1].twinx()
        tap_ax.plot(
            x,
            by_t["tap_pu"],
            color="tab:purple",
            linewidth=1.6,
            label="Average regulator tap",
        )
        tap_ax.set_ylabel("Average tap (pu)")

        lines_left, labels_left = axes[1].get_legend_handles_labels()
        lines_right, labels_right = tap_ax.get_legend_handles_labels()
        axes[1].legend(lines_left + lines_right, labels_left + labels_right, loc="best")

        fidvr_plot_path = out_dir / f"{log_stem}_fidvr_trajectory.png"
        fig.tight_layout()
        fig.savefig(fidvr_plot_path, dpi=300)
        plt.close(fig)
        print(f"[OK] Saved plot: {fidvr_plot_path}")

    alert_csv_path = out_dir / f"{log_stem}_fidvr_alerts.csv"
    alerts.to_csv(alert_csv_path, index=False)
    csv_path = out_dir / f"{log_stem}_distribution_voltage.csv"
    by_t.to_csv(csv_path, index=False)

    for line in alert_summary_lines(alerts, f"Feeder {feeder} {alert_signal_label}"):
        print(f"[INFO] {line}")
    print(f"[OK] Saved CSV: {alert_csv_path}")
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
    make_plots(df, out_dir, log_path.stem, log_path)


if __name__ == "__main__":
    main()
