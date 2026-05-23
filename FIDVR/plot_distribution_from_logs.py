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
import numpy as np
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
    r"Caps=(?P<caps_status>on|partial|off)\s+"
    r"(?:CapFrac=(?P<cap_fraction>[0-9.+\-eE]+)\s+)?"
    r"Tap=(?P<tap_pu>[0-9.+\-eE]+)\s+"
    r"Restore=(?P<restore_frac>[0-9.+\-eE]+)"
    r"(?:\s+ThermalRestore=(?P<thermal_restore_frac>[0-9.+\-eE]+))?"
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
    r"Restoring=(?P<motor_restoring_groups>\d+)"
    r"(?:\s+ContactorTrips=(?P<motor_contactor_open_groups>\d+))?"
    r"(?:\s+ThermalTrips=(?P<motor_thermal_trip_groups>\d+))?"
    r"(?:\s+LockedOut=(?P<motor_locked_out_groups>\d+))?"
    r"(?:\s+Dyn=(?P<dynamics_enabled>on|off))?"
)
REG_TAP_RE = re.compile(r"Tap(?P<name>[A-Za-z0-9_]+)=(?P<value>[0-9.+\-eE]+)")
CAP_STATE_RE = re.compile(r"\b(?P<name>Cap[0-9][A-Za-z0-9_]*)=(?P<state>on|off)\b")
CAP_VOLTAGE_RE = re.compile(r"\b(?P<name>Cap[0-9][A-Za-z0-9_]*)V=(?P<value>[0-9.+\-eE]+)")
CAP_LOCK_RE = re.compile(r"\b(?P<name>Cap[0-9][A-Za-z0-9_]*)Lock=(?P<value>[01])\b")
CAP_ACTION_RE = re.compile(r"\b(?P<name>Cap[0-9][A-Za-z0-9_]*)Action=(?P<action>[A-Za-z_]+)\b")

FIDVR_STAGE_LABELS = {
    "FAULT_ACTIVE": "Fault",
    "STALLED_MOTORS": "Stalled Motors",
    "OVERSHOOT": "Regulator Tap Boost",
    "CAPS_OFF": "Capacitors Off",
    "LOAD_RESTORATION": "Load Restoration",
}

FIDVR_STAGE_COLORS = {
    "FAULT_ACTIVE": "#d73027",
    "STALLED_MOTORS": "#fc8d59",
    "OVERSHOOT": "#91bfdb",
    "CAPS_OFF": "#4575b4",
    "LOAD_RESTORATION": "#74add1",
}
MOTOR_TRIPPED_COLOR = "#7b3294"
CAPACITOR_ON_COLOR = "#2ca25f"
CAP_FULL_ON_MARKER_COLOR = "#1b9e77"
RESTART_COMPLETE_MARKER_COLOR = "#238b45"
TAP_MAX_MARKER_COLOR = "#756bb1"

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


def _set_slide_voltage_limits(ax, series_list, upper_min: float = 1.08, lower_context: float = 0.4):
    values = pd.concat(series_list, axis=0).dropna()
    if values.empty:
        return

    vmin = float(values.min())
    vmax = float(values.max())
    span = max(vmax - vmin, 0.01)
    pad = max(0.005, 0.06 * span)
    ymin = max(0.0, min(lower_context, vmin - pad))
    ymax = max(upper_min, vmax + pad)
    ax.set_ylim(ymin, ymax)


def _apply_manual_xlim(ax_or_axes, x_limits):
    if x_limits is None:
        return
    if isinstance(ax_or_axes, np.ndarray):
        for axis in ax_or_axes.flat:
            axis.set_xlim(*x_limits)
        return
    if isinstance(ax_or_axes, (list, tuple)):
        for axis in ax_or_axes:
            axis.set_xlim(*x_limits)
        return
    ax_or_axes.set_xlim(*x_limits)


def _apply_manual_voltage_ylim(ax, voltage_y_limits):
    if voltage_y_limits is None:
        return
    ax.set_ylim(*voltage_y_limits)


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


def _fidvr_stage_series_with_cap_actions(by_t: pd.DataFrame):
    if "fidvr_stage" not in by_t.columns:
        return pd.Series(dtype=object)

    stage_series = by_t["fidvr_stage"].fillna("DISABLED")
    if "cap_fraction" in by_t.columns:
        cap_fraction = pd.to_numeric(by_t["cap_fraction"], errors="coerce")
        cap_action_mask = (
            cap_fraction.lt(0.99)
            & ~stage_series.isin(["DISABLED", "BASELINE", "FAULT_ACTIVE"])
        )
        stage_series = stage_series.mask(cap_action_mask, "CAPS_OFF")
    return stage_series


def _extract_fidvr_stage_intervals(by_t: pd.DataFrame):
    if "fidvr_stage" not in by_t.columns:
        return []

    neutral_stages = {"DISABLED", "BASELINE", "RECOVERED"}
    stage_series = _fidvr_stage_series_with_cap_actions(by_t)
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


def _add_alert_threshold_lines(ax, reference_voltage_pu: float, add_labels: bool = True):
    stall_threshold_pu = reference_voltage_pu * DEFAULT_STALL_ALERT_VOLTAGE_PU
    overvoltage_threshold_pu = reference_voltage_pu * DEFAULT_OVERVOLTAGE_ALERT_PU
    ax.axhline(
        stall_threshold_pu,
        color=ALERT_COLORS["Alert.2"],
        linewidth=1.0,
        linestyle=":",
        label=f"Alert.2 threshold ({stall_threshold_pu:.3f} pu)" if add_labels else None,
    )
    ax.axhline(
        overvoltage_threshold_pu,
        color=ALERT_COLORS["Alert.3"],
        linewidth=1.0,
        linestyle=":",
        label=f"Alert.3 threshold ({overvoltage_threshold_pu:.3f} pu)" if add_labels else None,
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


def _add_compact_alert_markers(ax, alerts: pd.DataFrame, xlabel: str, show_labels: bool = True):
    for row in alerts.itertuples(index=False):
        if not bool(row.triggered):
            continue
        x_pos = _time_value_in_plot_units(float(row.trigger_time_s), xlabel)
        color = ALERT_COLORS.get(row.alert_id, "black")
        ax.axvline(x_pos, color=color, linewidth=1.15, linestyle="--", alpha=0.78)
        if not show_labels:
            continue
        ax.annotate(
            row.alert_id,
            xy=(x_pos, 1.0),
            xycoords=("data", "axes fraction"),
            xytext=(0, 5),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=7.0,
            color=color,
            clip_on=False,
        )


def _bar_segments_from_intervals(intervals, xlabel: str):
    segments = []
    for start, end in intervals:
        x_start = _time_value_in_plot_units(float(start), xlabel)
        x_end = _time_value_in_plot_units(float(end), xlabel)
        width = max(0.0, x_end - x_start)
        if width > 0.0:
            segments.append((x_start, width))
    return segments


def _extract_positive_count_intervals(by_t: pd.DataFrame, column: str):
    if column not in by_t.columns or by_t[column].dropna().empty:
        return []

    times = pd.to_numeric(by_t["t_granted"], errors="coerce")
    counts = pd.to_numeric(by_t[column], errors="coerce").fillna(0)
    return _extract_boolean_intervals(times, counts > 0)


def _extract_boolean_intervals(times: pd.Series, active_mask: pd.Series):
    intervals = []
    active = False
    start = None

    for time_s, is_active in zip(times, active_mask):
        if not math.isfinite(float(time_s)):
            continue
        if is_active and not active:
            start = float(time_s)
            active = True
        elif active and not is_active:
            end = float(time_s)
            if start is not None and end > start:
                intervals.append((start, end))
            start = None
            active = False

    if active and start is not None and not times.dropna().empty:
        end = float(times.dropna().iloc[-1])
        if end > start:
            intervals.append((start, end))
    return intervals


def _extract_capacitor_state_intervals(by_t: pd.DataFrame, enabled: bool):
    if "cap_fraction" not in by_t.columns or by_t["cap_fraction"].dropna().empty:
        return []

    times = pd.to_numeric(by_t["t_granted"], errors="coerce")
    cap_fraction = pd.to_numeric(by_t["cap_fraction"], errors="coerce")
    if enabled:
        active_mask = cap_fraction.ge(0.99)
    else:
        active_mask = cap_fraction.lt(0.99)
    return _extract_boolean_intervals(times, active_mask.fillna(False))


def _fault_end_time_s(disturbance_intervals) -> float | None:
    if not isinstance(disturbance_intervals, dict):
        return None

    ends = []
    for key in ("fault_intervals", "line_intervals"):
        for _, end in disturbance_intervals.get(key, []):
            if math.isfinite(float(end)):
                ends.append(float(end))
    return max(ends) if ends else None


def _extract_restartable_motor_interval(
    by_t: pd.DataFrame,
    disturbance_intervals,
):
    """Return the Frst/Vrst/Trst restart window before long-delay restoration."""

    if "restore_frac" not in by_t.columns or by_t["restore_frac"].dropna().empty:
        return []

    times = pd.to_numeric(by_t["t_granted"], errors="coerce")
    restore = pd.to_numeric(by_t["restore_frac"], errors="coerce")
    thermal_restore = (
        pd.to_numeric(by_t["thermal_restore_frac"], errors="coerce").fillna(0.0)
        if "thermal_restore_frac" in by_t.columns
        else pd.Series(0.0, index=by_t.index)
    )

    valid = times.notna() & restore.notna()
    fault_end = _fault_end_time_s(disturbance_intervals)
    if fault_end is not None:
        valid &= times > fault_end + 1e-9
    valid &= thermal_restore <= 1e-6

    post_fault = pd.DataFrame({"time": times[valid], "restore": restore[valid]}).dropna()
    if post_fault.empty:
        return []

    peak_restore = float(post_fault["restore"].max())
    if peak_restore <= 0.01:
        return []

    start_rows = post_fault.loc[post_fault["restore"] > 0.005]
    end_rows = post_fault.loc[post_fault["restore"] >= peak_restore - 0.002]
    if start_rows.empty or end_rows.empty:
        return []

    start = float(start_rows["time"].iloc[0])
    end = float(end_rows["time"].iloc[0])
    if end <= start:
        end = float(post_fault["time"].iloc[-1])
    return [(start, end)] if end > start else []


def _restartable_motor_end_time(by_t: pd.DataFrame, disturbance_intervals):
    intervals = _extract_restartable_motor_interval(by_t, disturbance_intervals)
    return intervals[0][1] if intervals else None


def _capacitor_full_on_time(by_t: pd.DataFrame):
    if "cap_fraction" not in by_t.columns or by_t["cap_fraction"].dropna().empty:
        return None

    times = pd.to_numeric(by_t["t_granted"], errors="coerce")
    cap_fraction = pd.to_numeric(by_t["cap_fraction"], errors="coerce")
    valid = times.notna() & cap_fraction.notna()
    if not valid.any():
        return None

    was_off = cap_fraction.lt(0.99).cummax()
    switched_on = valid & was_off & cap_fraction.ge(0.99)
    rows = times.loc[switched_on]
    return float(rows.iloc[0]) if not rows.empty else None


def _regulator_max_tap_time(by_t: pd.DataFrame):
    if "tap_pu" not in by_t.columns or by_t["tap_pu"].dropna().empty:
        return None

    times = pd.to_numeric(by_t["t_granted"], errors="coerce")
    tap = pd.to_numeric(by_t["tap_pu"], errors="coerce")
    valid = times.notna() & tap.notna()
    if not valid.any():
        return None

    tap_valid = tap.loc[valid]
    max_tap = float(tap_valid.max())
    min_tap = float(tap_valid.min())
    if max_tap <= min_tap + 1e-4:
        return None

    rows = times.loc[valid & tap.ge(max_tap - 1e-5)]
    return float(rows.iloc[0]) if not rows.empty else None


def _recovery_event_markers(by_t: pd.DataFrame, disturbance_intervals):
    events = []
    cap_time = _capacitor_full_on_time(by_t)
    if cap_time is not None:
        events.append(("Caps fully on", cap_time, CAP_FULL_ON_MARKER_COLOR))

    restart_done_time = _restartable_motor_end_time(by_t, disturbance_intervals)
    if restart_done_time is not None:
        events.append(
            ("20% restart complete", restart_done_time, RESTART_COMPLETE_MARKER_COLOR)
        )

    tap_time = _regulator_max_tap_time(by_t)
    if tap_time is not None:
        events.append(("Regulator tap max", tap_time, TAP_MAX_MARKER_COLOR))

    return events


def _add_event_marker(
    ax,
    event_time_s,
    xlabel: str,
    color: str,
    label: str | None = None,
):
    if event_time_s is None or not math.isfinite(float(event_time_s)):
        return

    x_pos = _time_value_in_plot_units(float(event_time_s), xlabel)
    ax.axvline(
        x_pos,
        color=color,
        linewidth=1.15,
        linestyle=(0, (3, 2)),
        alpha=0.85,
        label=label,
    )


def _add_recovery_event_markers(
    ax,
    events,
    xlabel: str,
    show_labels: bool = True,
):
    for label, event_time_s, color in events:
        legend_label = f"{label} ({event_time_s:.2f} s)" if show_labels else None
        _add_event_marker(ax, event_time_s, xlabel, color, legend_label)


def _add_timeline_row(ax, y_pos: int, segments, color: str, label: str):
    if not segments:
        return
    ax.broken_barh(
        segments,
        (y_pos - 0.34, 0.68),
        facecolors=color,
        edgecolors=color,
        alpha=0.28,
        linewidth=1.0,
    )


def _add_fidvr_timeline(
    ax,
    stage_intervals,
    disturbance_intervals,
    alerts: pd.DataFrame,
    xlabel: str,
    by_t: pd.DataFrame | None = None,
):
    rows = [
        ("Fault", "FAULT_ACTIVE", FIDVR_STAGE_COLORS["FAULT_ACTIVE"]),
        ("Capacitors off", "CAPS_OFF_STATE", FIDVR_STAGE_COLORS["CAPS_OFF"]),
        ("Capacitors on", "CAPS_ON_STATE", CAPACITOR_ON_COLOR),
        ("Stalled motors", "STALLED_MOTORS", FIDVR_STAGE_COLORS["STALLED_MOTORS"]),
        ("Contactor opened", "CONTACTOR_OPEN", "#8c8c8c"),
        ("Thermal trip", "MOTOR_TRIPPED", MOTOR_TRIPPED_COLOR),
        ("Regulator tap boost", "OVERSHOOT", FIDVR_STAGE_COLORS["OVERSHOOT"]),
        ("Load restoration", "LOAD_RESTORATION", FIDVR_STAGE_COLORS["LOAD_RESTORATION"]),
        ("Alert.3 active", "ALERT3_ACTIVE", ALERT_COLORS["Alert.3"]),
    ]

    stage_map = {stage: [] for _, stage, _ in rows}
    for start, end, stage in stage_intervals:
        if stage in stage_map:
            stage_map[stage].append((start, end))

    if isinstance(disturbance_intervals, dict):
        stage_map["FAULT_ACTIVE"].extend(disturbance_intervals.get("fault_intervals", []))
        stage_map["FAULT_ACTIVE"].extend(disturbance_intervals.get("line_intervals", []))
    if by_t is not None:
        stage_map["CAPS_OFF_STATE"].extend(
            _extract_capacitor_state_intervals(by_t, enabled=False)
        )
        stage_map["CAPS_ON_STATE"].extend(
            _extract_capacitor_state_intervals(by_t, enabled=True)
        )
        # Use the actual Motor D state counters for this row.  The dominant
        # FIDVR stage can be "Caps off" while the motors are already stalled.
        stage_map["STALLED_MOTORS"].extend(
            _extract_positive_count_intervals(by_t, "motor_stalled_groups")
        )
        contactor_intervals = _extract_positive_count_intervals(
            by_t, "motor_contactor_open_groups"
        )
        thermal_intervals = _extract_positive_count_intervals(
            by_t, "motor_thermal_trip_groups"
        )
        if contactor_intervals or thermal_intervals:
            stage_map["CONTACTOR_OPEN"].extend(contactor_intervals)
            stage_map["MOTOR_TRIPPED"].extend(thermal_intervals)
        else:
            stage_map["MOTOR_TRIPPED"].extend(
                _extract_positive_count_intervals(by_t, "motor_tripped_groups")
            )

    alert3_rows = alerts.loc[alerts["alert_id"] == "Alert.3"]
    if not alert3_rows.empty and bool(alert3_rows.iloc[0]["triggered"]):
        row = alert3_rows.iloc[0]
        start = float(row["start_time_s"])
        end = float(row["end_time_s"])
        if math.isfinite(start) and math.isfinite(end):
            stage_map["ALERT3_ACTIVE"].append((start, end))

    for y_pos, (label, stage, color) in enumerate(rows):
        segments = _bar_segments_from_intervals(stage_map.get(stage, []), xlabel)
        _add_timeline_row(ax, y_pos, segments, color, label)

    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([label for label, _, _ in rows])
    ax.set_ylim(-0.6, len(rows) - 0.4)
    ax.invert_yaxis()
    ax.grid(True, axis="x", alpha=0.24)
    ax.grid(False, axis="y")
    ax.set_xlabel(xlabel)
    ax.tick_params(axis="x", labelsize=8)
    ax.tick_params(axis="y", labelsize=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def _plot_tracked_bus_voltage_panel(
    ax,
    x,
    by_t: pd.DataFrame,
    dist_metric: str,
    dist_metric_label: str,
    has_vpos: bool,
    reference_voltage_pu: float,
    alerts: pd.DataFrame,
    xlabel: str,
    threshold_labels: bool = True,
    alert_labels: bool = True,
):
    ax.plot(x, by_t[dist_metric], color="tab:green", linewidth=2.0, label=dist_metric_label)
    if has_vpos and dist_metric != "vavg_pu":
        ax.plot(
            x,
            by_t["vavg_pu"],
            color="tab:olive",
            linewidth=1.15,
            linestyle=":",
            label=f"{by_t['dist_bus'].dropna().iloc[-1]} avg |V|",
        )
    _add_alert_threshold_lines(ax, reference_voltage_pu, add_labels=threshold_labels)
    if alert_labels:
        _add_compact_alert_markers(ax, alerts, xlabel)
    ax.grid(True, color="0.86", linewidth=0.8)
    ax.tick_params(axis="both", labelsize=8.5)


def _trigger_time(alerts: pd.DataFrame, alert_id: str):
    rows = alerts.loc[(alerts["alert_id"] == alert_id) & (alerts["triggered"])]
    if rows.empty:
        return None
    return float(rows.iloc[0]["trigger_time_s"])


def _alerts_in_time_window(alerts: pd.DataFrame, start_s: float, end_s: float):
    return alerts.loc[
        (alerts["triggered"])
        & (alerts["trigger_time_s"] >= start_s)
        & (alerts["trigger_time_s"] <= end_s)
    ]


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
            caps_status = fidvr.group("caps_status")
            row.update(
                {
                    "fidvr_stage": fidvr.group("fidvr_stage"),
                    "tx_v_pu": float(fidvr.group("tx_v_pu")),
                    "motor_p_scale": float(fidvr.group("motor_p_scale")),
                    "motor_q_scale": float(fidvr.group("motor_q_scale")),
                    "caps_status": caps_status,
                    "caps_on": caps_status != "off",
                    "cap_fraction": float(fidvr.group("cap_fraction"))
                    if fidvr.group("cap_fraction") is not None
                    else (
                        1.0
                        if caps_status == "on"
                        else 0.5
                        if caps_status == "partial"
                        else 0.0
                    ),
                    "tap_pu": float(fidvr.group("tap_pu")),
                    "restore_frac": float(fidvr.group("restore_frac")),
                    "thermal_restore_frac": float(fidvr.group("thermal_restore_frac"))
                    if fidvr.group("thermal_restore_frac") is not None
                    else 0.0,
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
                    "motor_contactor_open_groups": int(
                        motor_state.group("motor_contactor_open_groups") or 0
                    ),
                    "motor_thermal_trip_groups": int(
                        motor_state.group("motor_thermal_trip_groups") or 0
                    ),
                    "motor_locked_out_groups": int(
                        motor_state.group("motor_locked_out_groups") or 0
                    ),
                    "dynamics_enabled": motor_state.group("dynamics_enabled") == "on"
                    if motor_state.group("dynamics_enabled") is not None
                    else False,
                }
            )

        for tap_match in REG_TAP_RE.finditer(line):
            tap_name = tap_match.group("name")
            tap_key = f"tap_{tap_name.lower()}"
            row[tap_key] = float(tap_match.group("value"))

        for cap_match in CAP_STATE_RE.finditer(line):
            cap_key = cap_match.group("name").lower()
            state = cap_match.group("state")
            row[f"{cap_key}_state"] = state
            row[f"{cap_key}_enabled"] = state == "on"

        for cap_match in CAP_VOLTAGE_RE.finditer(line):
            cap_key = cap_match.group("name").lower()
            row[f"{cap_key}_v_pu"] = float(cap_match.group("value"))

        for cap_match in CAP_LOCK_RE.finditer(line):
            cap_key = cap_match.group("name").lower()
            row[f"{cap_key}_locked"] = cap_match.group("value") == "1"

        for cap_match in CAP_ACTION_RE.finditer(line):
            cap_key = cap_match.group("name").lower()
            row[f"{cap_key}_action"] = cap_match.group("action")

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


def make_plots(
    df: pd.DataFrame,
    out_dir: Path,
    log_stem: str,
    log_path: Path,
    x_limits=None,
    voltage_y_limits=None,
):
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
    if "fidvr_stage" in by_t.columns:
        by_t["fidvr_stage_effective"] = _fidvr_stage_series_with_cap_actions(by_t)
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
    dist_metric = "vavg_pu"
    dist_metric_label = f"{dist_bus} avg |V|"
    show_vavg_overlay = has_vpos and dist_metric != "vavg_pu"
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
    contactor_intervals = _extract_positive_count_intervals(
        by_t, "motor_contactor_open_groups"
    )
    if contactor_intervals and "motor_contactor_open_groups" in by_t.columns:
        first_open_time = contactor_intervals[0][0]
        max_open = int(
            pd.to_numeric(by_t["motor_contactor_open_groups"], errors="coerce").max()
        )
        print(
            f"[INFO] Motor contactor indicator: first contactor opening at "
            f"t={first_open_time:.3f}s; max opened groups={max_open}."
        )
    motor_trip_intervals = _extract_positive_count_intervals(
        by_t, "motor_thermal_trip_groups"
    )
    if motor_trip_intervals and "motor_thermal_trip_groups" in by_t.columns:
        first_trip_time = motor_trip_intervals[0][0]
        max_tripped = int(
            pd.to_numeric(by_t["motor_thermal_trip_groups"], errors="coerce").max()
        )
        print(
            f"[INFO] Motor thermal trip indicator: first thermal trip at "
            f"t={first_trip_time:.3f}s; max thermally tripped groups={max_tripped}."
        )
    recovery_events = _recovery_event_markers(by_t, disturbance_intervals)
    for label, event_time, _ in recovery_events:
        print(f"[INFO] Recovery event: {label} at t={event_time:.3f}s.")

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
    if show_vavg_overlay:
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
    _add_recovery_event_markers(axes[0], recovery_events, xlabel)
    _add_fidvr_stage_overlays(axes[0], fidvr_stage_intervals, xlabel)
    _add_disturbance_overlays(axes[0], disturbance_intervals, xlabel, line_idx=disturbance_line_idx)
    axes[0].set_ylabel("Voltage (pu)")
    axes[0].set_title(
        f"Feeder {feeder}: Interface vs distribution-side voltage "
        f"(alerts on {alert_signal_label})"
    )
    axes[0].grid(True)
    voltage_series = [by_t["source_v_pu"], by_t[dist_metric]]
    if show_vavg_overlay:
        voltage_series.append(by_t["vavg_pu"])
    _set_voltage_limits(axes[0], voltage_series)
    _apply_manual_voltage_ylim(axes[0], voltage_y_limits)
    axes[0].legend()

    axes[1].plot(x, by_t["va_pu"], label="Phase A", linewidth=1.6)
    axes[1].plot(x, by_t["vb_pu"], label="Phase B", linewidth=1.6)
    axes[1].plot(x, by_t["vc_pu"], label="Phase C", linewidth=1.6)
    axes[1].plot(x, by_t["vavg_pu"], label="Average", linestyle="--", linewidth=1.8)
    if show_vavg_overlay:
        axes[1].plot(x, by_t["vpos_pu"], label="Positive sequence", linestyle="-.", linewidth=1.8)
    _add_alert_threshold_lines(axes[1], reference_voltage_pu)
    _add_alert_window_overlay(axes[1], alerts, xlabel)
    _add_alert_overlays(axes[1], alerts, xlabel)
    _add_recovery_event_markers(axes[1], recovery_events, xlabel, show_labels=False)
    _add_fidvr_stage_overlays(axes[1], fidvr_stage_intervals, xlabel)
    _add_disturbance_overlays(axes[1], disturbance_intervals, xlabel)
    axes[1].set_ylabel("Voltage (pu)")
    axes[1].set_xlabel(xlabel)
    axes[1].set_title(f"Distribution bus {dist_bus} voltage by phase")
    _set_voltage_limits(
        axes[1],
        [by_t["va_pu"], by_t["vb_pu"], by_t["vc_pu"], by_t["vavg_pu"]]
        + ([by_t["vpos_pu"]] if show_vavg_overlay else []),
    )
    _apply_manual_voltage_ylim(axes[1], voltage_y_limits)
    axes[1].grid(True)
    axes[1].legend()

    fig.suptitle("OpenDSS Distribution-Side Voltage vs Time", fontsize=14)
    _apply_manual_xlim(axes, x_limits)
    plt.tight_layout()

    plot_path = out_dir / f"{log_stem}_distribution_voltage_vs_time.png"
    fig.savefig(plot_path, dpi=300)
    plt.close(fig)

    # Save a slide-friendly plot for the tracked bus. Keep the voltage panel
    # clean and move the FIDVR stages into a separate timeline band.
    fig, (ax, timeline_ax) = plt.subplots(
        2,
        1,
        sharex=True,
        figsize=(10.5, 5.3),
        gridspec_kw={"height_ratios": [3.1, 1.15]},
    )
    ax.plot(x, by_t[dist_metric], color="tab:green", linewidth=2.2, label=dist_metric_label)
    if show_vavg_overlay:
        ax.plot(
            x,
            by_t["vavg_pu"],
            color="tab:olive",
            linewidth=1.3,
            linestyle=":",
            label=f"{dist_bus} avg |V|",
        )
    _add_alert_threshold_lines(ax, reference_voltage_pu)
    _add_compact_alert_markers(ax, alerts, xlabel)
    _add_recovery_event_markers(ax, recovery_events, xlabel)
    ax.set_ylabel("Average voltage (pu)")
    single_voltage_series = [by_t[dist_metric]] + ([by_t["vavg_pu"]] if show_vavg_overlay else [])
    _set_slide_voltage_limits(ax, single_voltage_series, upper_min=1.08)
    ax.grid(True, color="0.86", linewidth=0.8)
    ax.tick_params(axis="both", labelsize=9)
    ax.legend(
        loc="center left",
        bbox_to_anchor=(1.01, 0.5),
        fontsize=8,
        framealpha=0.95,
        title="Signals",
        title_fontsize=8,
    )
    _apply_manual_voltage_ylim(ax, voltage_y_limits)

    _add_fidvr_timeline(
        timeline_ax,
        fidvr_stage_intervals,
        disturbance_intervals,
        alerts,
        xlabel,
        by_t=by_t,
    )
    _add_recovery_event_markers(timeline_ax, recovery_events, xlabel, show_labels=False)

    single_plot_path = out_dir / f"{log_stem}_distribution_bus_voltage_vs_time.png"
    _apply_manual_xlim([ax, timeline_ax], x_limits)
    fig.subplots_adjust(left=0.10, right=0.80, top=0.88, bottom=0.12, hspace=0.12)
    fig.savefig(single_plot_path, dpi=300, bbox_inches="tight")
    plt.close(fig)

    # A second slide view keeps the full trace but adds zoom panels for the two
    # dense parts of the FIDVR story: fault/stall and late overvoltage.
    alert2_time = _trigger_time(alerts, "Alert.2")
    alert3_time = _trigger_time(alerts, "Alert.3")
    t_min_s = float(by_t["t_granted"].min())
    t_max_s = float(by_t["t_granted"].max())
    early_start_s = max(t_min_s, (fault_intervals[0][0] if fault_intervals else t_min_s) - 0.25)
    early_end_s = min(t_max_s, (alert2_time + 1.25) if alert2_time is not None else early_start_s + 7.0)
    late_anchor_s = alert3_time if alert3_time is not None else t_max_s
    late_start_s = max(t_min_s, late_anchor_s - 8.0)
    late_end_s = t_max_s
    early_xlim = (
        _time_value_in_plot_units(early_start_s, xlabel),
        _time_value_in_plot_units(early_end_s, xlabel),
    )
    late_xlim = (
        _time_value_in_plot_units(late_start_s, xlabel),
        _time_value_in_plot_units(late_end_s, xlabel),
    )

    fig = plt.figure(figsize=(10.8, 6.3))
    grid = fig.add_gridspec(
        3,
        2,
        height_ratios=[2.25, 1.55, 1.05],
        hspace=0.40,
        wspace=0.22,
    )
    overview_ax = fig.add_subplot(grid[0, :])
    early_ax = fig.add_subplot(grid[1, 0])
    late_ax = fig.add_subplot(grid[1, 1])
    stage_ax = fig.add_subplot(grid[2, :])

    _plot_tracked_bus_voltage_panel(
        overview_ax,
        x,
        by_t,
        dist_metric,
        dist_metric_label,
        has_vpos,
        reference_voltage_pu,
        alerts,
        xlabel,
    )
    _add_recovery_event_markers(overview_ax, recovery_events, xlabel)
    overview_ax.set_ylabel("Average voltage (pu)", fontsize=9)
    overview_voltage_series = [by_t[dist_metric]] + ([by_t["vavg_pu"]] if show_vavg_overlay else [])
    _set_slide_voltage_limits(overview_ax, overview_voltage_series, upper_min=1.08)
    overview_ax.legend(
        loc="center left",
        bbox_to_anchor=(1.01, 0.5),
        fontsize=7.5,
        framealpha=0.95,
        title="Signals",
        title_fontsize=7.5,
    )

    _plot_tracked_bus_voltage_panel(
        early_ax,
        x,
        by_t,
        dist_metric,
        dist_metric_label,
        has_vpos,
        reference_voltage_pu,
        alerts,
        xlabel,
        threshold_labels=False,
        alert_labels=False,
    )
    _add_recovery_event_markers(early_ax, recovery_events, xlabel, show_labels=False)
    early_ax.set_ylabel("Average voltage (pu)", fontsize=8.5)
    early_ax.set_xlim(*early_xlim)
    early_mask = (x >= early_xlim[0]) & (x <= early_xlim[1])
    early_series = [by_t.loc[early_mask, dist_metric]]
    if show_vavg_overlay:
        early_series.append(by_t.loc[early_mask, "vavg_pu"])
    _set_slide_voltage_limits(early_ax, early_series, upper_min=1.02)
    _add_compact_alert_markers(
        early_ax,
        _alerts_in_time_window(alerts, early_start_s, early_end_s),
        xlabel,
        show_labels=False,
    )

    _plot_tracked_bus_voltage_panel(
        late_ax,
        x,
        by_t,
        dist_metric,
        dist_metric_label,
        has_vpos,
        reference_voltage_pu,
        alerts,
        xlabel,
        threshold_labels=False,
        alert_labels=False,
    )
    _add_recovery_event_markers(late_ax, recovery_events, xlabel, show_labels=False)
    late_ax.set_xlim(*late_xlim)
    late_mask = (x >= late_xlim[0]) & (x <= late_xlim[1])
    late_series = [by_t.loc[late_mask, dist_metric]]
    if show_vavg_overlay:
        late_series.append(by_t.loc[late_mask, "vavg_pu"])
    _set_voltage_limits(late_ax, late_series)
    late_ymin, late_ymax = late_ax.get_ylim()
    late_ymin = max(0.94, late_ymin)
    late_ymax = min(1.08, max(late_ymax, late_ymin + 0.02))
    late_ax.set_ylim(late_ymin, late_ymax)
    _add_compact_alert_markers(
        late_ax,
        _alerts_in_time_window(alerts, late_start_s, late_end_s),
        xlabel,
        show_labels=False,
    )

    _add_fidvr_timeline(stage_ax, fidvr_stage_intervals, disturbance_intervals, alerts, xlabel, by_t=by_t)
    _add_recovery_event_markers(stage_ax, recovery_events, xlabel, show_labels=False)
    _apply_manual_xlim([overview_ax, stage_ax], x_limits)
    _apply_manual_voltage_ylim(overview_ax, voltage_y_limits)
    _apply_manual_voltage_ylim(early_ax, voltage_y_limits)
    _apply_manual_voltage_ylim(late_ax, voltage_y_limits)
    fig.subplots_adjust(left=0.08, right=0.80, top=0.90, bottom=0.10)

    stage_plot_path = out_dir / f"{log_stem}_distribution_bus_voltage_stage_view.png"
    fig.savefig(stage_plot_path, dpi=300, bbox_inches="tight")
    plt.close(fig)

    if {
        "fidvr_stage",
        "motor_p_scale",
        "motor_q_scale",
        "caps_on",
        "cap_fraction",
        "tap_pu",
        "restore_frac",
        "thermal_restore_frac",
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
        if show_vavg_overlay:
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
        axes[1].plot(
            x,
            by_t["thermal_restore_frac"],
            label="Thermal restore fraction",
            linewidth=1.5,
        )
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
    parser.add_argument(
        "--xlim",
        type=float,
        nargs=2,
        metavar=("XMIN", "XMAX"),
        default=None,
        help="Optional shared x-axis limits in seconds for exported plots.",
    )
    parser.add_argument(
        "--voltage-ylim",
        type=float,
        nargs=2,
        metavar=("YMIN", "YMAX"),
        default=None,
        help="Optional shared y-axis limits in pu for voltage plots.",
    )
    args = parser.parse_args()

    log_path = Path(args.log).expanduser().resolve()
    if not log_path.exists():
        raise FileNotFoundError(f"Log not found: {log_path}")

    out_dir = Path(args.out).expanduser().resolve() if args.out else log_path.parent

    print(f"[INFO] Log: {log_path}")
    print(f"[INFO] Out: {out_dir}")
    if args.xlim is not None:
        print(f"[INFO] X limits: {tuple(args.xlim)}")
    if args.voltage_ylim is not None:
        print(f"[INFO] Voltage Y limits: {tuple(args.voltage_ylim)}")

    df = parse_distribution_log(log_path)
    make_plots(
        df,
        out_dir,
        log_path.stem,
        log_path,
        x_limits=tuple(args.xlim) if args.xlim is not None else None,
        voltage_y_limits=tuple(args.voltage_ylim)
        if args.voltage_ylim is not None
        else None,
    )


if __name__ == "__main__":
    main()
