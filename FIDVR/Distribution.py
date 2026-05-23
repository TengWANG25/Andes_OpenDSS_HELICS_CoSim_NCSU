#!/usr/bin/env python3
"""OpenDSS feeder federate for the ANDES-OpenDSS-HELICS FIDVR study.

The feeder receives the transmission interface voltage from HELICS, updates the
WECC Motor D state, solves an OpenDSS snapshot, and publishes the
resulting feeder complex power back to the transmission federate.
"""

# Import libraries 
import csv
import copy
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import helics as h
import opendssdirect as dss

from fidvr_controls import (
    DelayedShuntControlConfig,
    DelayedShuntControlState,
    shunt_status_from_fraction,
    update_delayed_shunt_control,
)
from fidvr_alerts import FidvrAlertDetector, alert_summary_lines
from fidvr_load_restoration import (
    ThermalLoadRestorationConfig,
    ThermalLoadRestorationState,
    update_thermal_load_restoration,
)


ITER_STATE_NAME = {
    h.HELICS_ITERATION_RESULT_NEXT_STEP: "NEXT_STEP",
    h.HELICS_ITERATION_RESULT_ITERATING: "ITERATING",
    h.HELICS_ITERATION_RESULT_ERROR: "ERROR",
    h.HELICS_ITERATION_RESULT_HALTED: "HALTED",
}

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

ALL_BUS_VOLTAGE_COLUMNS = [
    "feeder",
    "iter",
    "t_granted",
    "state",
    "bus",
    "node_count",
    "vavg_pu",
    "vpos_pu",
    "vneg_pu",
    "vzero_pu",
    "va_pu",
    "vb_pu",
    "vc_pu",
    "anga_deg",
    "angb_deg",
    "angc_deg",
]

# =============================================================================
# Data Models
# =============================================================================

@dataclass(frozen=True)
class LoadSpec:
    name: str
    kw: float
    kvar: float
    bus: str
    kv: float
    phases: int
    conn: str


@dataclass(frozen=True)
class RegulatorSpec:
    name: str
    baseline_tap: float
    min_tap: float
    max_tap: float
    tap_step: float
    monitor_bus: str
    phase: int


@dataclass(frozen=True)
class CapacitorSpec:
    name: str
    bus: str
    phases: int
    kv: float
    base_kvar: float
    applied_kvar: float


@dataclass(frozen=True)
class DisturbanceConfig:
    enabled: bool
    fault_time: float
    fault_duration: float
    clear_time: float


@dataclass(frozen=True)
class MotorElementSpec:
    """OpenDSS load element used as one WECC Motor D terminal injection."""

    element_name: str
    source_load_name: str
    group_index: int
    phase: int
    phases: int
    kw: float
    kva: float
    baseline_kvar: float
    stall_kw: float
    stall_kvar: float
    bus: str
    kv: float
    conn: str


@dataclass
class FeederRuntimeState:
    """Mutable state that must persist across HELICS granted times."""

    motor_elements: tuple[MotorElementSpec, ...] = ()
    motor_group_states: dict[str, str] = field(default_factory=dict)
    motor_group_p_scales: dict[str, float] = field(default_factory=dict)
    motor_group_q_scales: dict[str, float] = field(default_factory=dict)
    motor_stall_armed_since: dict[str, float | None] = field(default_factory=dict)
    motor_thermal_state: dict[str, float] = field(default_factory=dict)
    motor_trip_reason: dict[str, str] = field(default_factory=dict)
    motor_reconnect_armed_since: dict[str, float | None] = field(default_factory=dict)
    motor_restore_frac: dict[str, float] = field(default_factory=dict)
    motor_contactor_fraction: dict[str, float] = field(default_factory=dict)
    motor_uv_trip_fraction: dict[str, float] = field(default_factory=dict)
    motor_uv1_armed_since: dict[str, float | None] = field(default_factory=dict)
    motor_uv2_armed_since: dict[str, float | None] = field(default_factory=dict)
    motor_thermal_state_a: dict[str, float] = field(default_factory=dict)
    motor_thermal_state_b: dict[str, float] = field(default_factory=dict)
    motor_wecc_a_stalled: dict[str, bool] = field(default_factory=dict)
    motor_wecc_b_stalled: dict[str, bool] = field(default_factory=dict)
    motor_wecc_b_restarted: dict[str, bool] = field(default_factory=dict)
    motor_thermal_restore_trip_time: dict[str, float | None] = field(default_factory=dict)
    motor_thermal_restore_started_at: dict[str, float | None] = field(default_factory=dict)
    motor_thermal_restore_frac: dict[str, float] = field(default_factory=dict)
    motor_thermal_restore_target: dict[str, float] = field(default_factory=dict)
    motor_thermal_restore_delay_s: dict[str, float] = field(default_factory=dict)
    regulator_low_armed_since: dict[str, float | None] = field(default_factory=dict)
    regulator_high_armed_since: dict[str, float | None] = field(default_factory=dict)
    regulator_last_action_time: dict[str, float] = field(default_factory=dict)
    capacitor_on_armed_since: dict[str, float | None] = field(default_factory=dict)
    capacitor_off_armed_since: dict[str, float | None] = field(default_factory=dict)
    capacitor_states: dict[str, bool] = field(default_factory=dict)
    capacitor_locked_out: dict[str, bool] = field(default_factory=dict)
    capacitor_last_action: dict[str, str] = field(default_factory=dict)
    capacitor_monitored_voltage: dict[str, float] = field(default_factory=dict)
    last_control_time: float = 0.0
    last_stage_info: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FidvrConfig:
    enabled: bool
    motor_loads: tuple[str, ...]
    capacitor_names: tuple[str, ...]
    regulator_names: tuple[str, ...]
    motor_share: float
    wecc_comp_pf: float
    wecc_vstall: float
    wecc_rstall: float
    wecc_xstall: float
    wecc_tstall: float
    wecc_frst: float
    wecc_vrst: float
    wecc_trst: float
    wecc_vbrk: float
    wecc_vc1off: float
    wecc_vc2off: float
    wecc_vc1on: float
    wecc_vc2on: float
    wecc_tth: float
    wecc_th1t: float
    wecc_th2t: float
    wecc_fuvr: float
    wecc_uvtr1: float
    wecc_ttr1: float
    wecc_uvtr2: float
    wecc_ttr2: float
    enable_thermal_load_restoration: bool
    thermal_restore_min_delay_s: float
    thermal_restore_max_delay_s: float
    thermal_restore_ramp_s: float
    thermal_restore_voltage_pu: float
    thermal_restore_dropout_voltage_pu: float
    thermal_restore_fraction: float
    regulator_low_voltage_pu: float
    regulator_high_voltage_pu: float
    regulator_monitor_bus: str
    regulator_delay_s: float
    regulator_tap_delay_s: float
    capacitor_on_voltage_pu: float
    capacitor_off_voltage_pu: float
    capacitor_on_delay_s: float
    capacitor_off_delay_s: float
    initial_capacitor_fraction: float
    capacitor_lockout_after_open: bool
    capacitor_kvar_scale: float
    enable_reg_control: bool
    enable_cap_control: bool
    alert_signal: str
    alert_bus: str
    prefault_min_voltage_pu: float


feeder_index = int(sys.argv[1]) if len(sys.argv) > 1 else 1


# =============================================================================
# Environment Configuration
# =============================================================================

def get_target_time() -> float:
    value = os.environ.get("SIM_TARGET_TIME", "10.0")
    try:
        target = float(value)
    except ValueError as exc:
        raise ValueError(
            f"Invalid SIM_TARGET_TIME='{value}'. Expected a positive float in seconds."
        ) from exc
    if target <= 0.0:
        raise ValueError(
            f"Invalid SIM_TARGET_TIME='{value}'. Expected a positive float in seconds."
        )
    return target


def get_positive_env_float(name: str, default: float) -> float:
    value = os.environ.get(name, str(default))
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(
            f"Invalid {name}='{value}'. Expected a positive float."
        ) from exc
    if parsed <= 0.0:
        raise ValueError(f"Invalid {name}='{value}'. Expected a positive float.")
    return parsed


def get_nonnegative_env_float(name: str, default: float) -> float:
    value = os.environ.get(name, str(default))
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(
            f"Invalid {name}='{value}'. Expected a non-negative float."
        ) from exc
    if parsed < 0.0:
        raise ValueError(f"Invalid {name}='{value}'. Expected a non-negative float.")
    return parsed


def get_fraction_env_float(name: str, default: float) -> float:
    value = get_nonnegative_env_float(name, default)
    if value > 1.0:
        raise ValueError(f"Invalid {name}='{value}'. Expected a value in [0, 1].")
    return value


def get_env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False

    raise ValueError(
        f"Invalid {name}='{value}'. Expected one of true/false, yes/no, 1/0."
    )


def get_env_choice(name: str, default: str, valid: set[str]) -> str:
    value = os.environ.get(name, default).strip().lower()
    if value not in valid:
        valid_str = ", ".join(sorted(valid))
        raise ValueError(f"Invalid {name}='{value}'. Expected one of: {valid_str}.")
    return value


def get_env_name_list(name: str, default: str) -> tuple[str, ...]:
    raw = os.environ.get(name, default)
    names = []
    for entry in raw.split(","):
        normalized = entry.strip().lower()
        if normalized:
            names.append(normalized)
    return tuple(names)


def get_cosim_step_config(disturbance: DisturbanceConfig):
    fine_dt = get_positive_env_float("SIM_FINE_DT", 0.005)
    coarse_dt = get_positive_env_float("SIM_COARSE_DT", 0.02)
    coarse_start_default = disturbance.clear_time + 0.5 if disturbance.enabled else 0.5
    coarse_start = get_positive_env_float("SIM_COARSE_START", coarse_start_default)
    if coarse_dt < fine_dt:
        raise ValueError(
            f"Invalid co-simulation step schedule: SIM_COARSE_DT={coarse_dt} "
            f"must be >= SIM_FINE_DT={fine_dt}."
        )
    return fine_dt, coarse_dt, coarse_start


def get_broker_url() -> str:
    return os.environ.get("HELICS_BROKER_URL", "tcp://127.0.0.1:23406")


def get_voltage_topic() -> str:
    return os.environ.get("TX_VOLTAGE_TOPIC", "TxInterfaceVoltage")


def get_feeder_power_topic(feeder_idx: int) -> str:
    prefix = os.environ.get("DIST_POWER_TOPIC_PREFIX", "Feeder")
    return f"{prefix}{feeder_idx}_Power"


def get_distribution_case_path(script_dir: Path) -> Path:
    case_value = os.environ.get("DIST_MASTER_DSS", "13Bus/IEEE13Nodeckt.dss")
    case_path = Path(case_value)
    if case_path.is_absolute():
        return case_path.resolve()

    # The FIDVR scripts may live in a subfolder while shared OpenDSS cases stay
    # at the repository root. Prefer the script-local path, then fall back to
    # the parent folder so the default 13Bus case survives that layout.
    for base_dir in (script_dir, script_dir.parent, Path.cwd()):
        candidate = (base_dir / case_path).resolve()
        if candidate.exists():
            return candidate

    return (script_dir / case_path).resolve()


def get_output_dir(script_dir: Path) -> Path:
    output_value = (
        os.environ.get("COSIM_OUTPUT_DIR")
        or os.environ.get("RUN_OUTPUT_DIR")
        or os.environ.get("FIDVR_OUTPUT_DIR")
    )
    if not output_value:
        return script_dir

    output_dir = Path(output_value)
    if not output_dir.is_absolute():
        output_dir = script_dir / output_dir
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def get_distribution_voltage_bus() -> str:
    return os.environ.get("DIST_VOLTAGE_BUS", "650").strip()


def get_all_bus_voltage_logging_enabled() -> bool:
    return get_env_bool("DIST_LOG_ALL_BUS_VOLTAGES", True)


def get_all_bus_voltage_buses() -> tuple[str, ...]:
    value = os.environ.get("DIST_ALL_BUS_VOLTAGE_BUSES", "ieee13").strip()
    if not value or value.lower() == "ieee13":
        return IEEE13_NODE_BUSES
    if value.lower() in {"all", "*"}:
        return tuple(str(name) for name in dss.Circuit.AllBusNames())
    return tuple(token for token in value.replace(",", " ").split() if token)


def get_cosim_base_mva() -> float:
    return get_positive_env_float("COSIM_BASE_MVA", 100.0)


def get_distribution_load_scale() -> float:
    return get_positive_env_float("DIST_LOAD_SCALE", 1.0)


def get_disturbance_config() -> DisturbanceConfig:
    enabled = get_env_bool("TX_ENABLE_DISTURBANCE", False)
    fault_time = get_positive_env_float(
        "TX_FAULT_TIME",
        get_positive_env_float("TX_DISTURBANCE_TIME", 1.0),
    )
    fault_duration = get_positive_env_float(
        "TX_FAULT_DURATION",
        get_positive_env_float("TX_DISTURBANCE_DURATION", 0.08),
    )
    clear_time = fault_time + fault_duration
    return DisturbanceConfig(
        enabled=enabled,
        fault_time=fault_time,
        fault_duration=fault_duration,
        clear_time=clear_time,
    )


def get_fidvr_config() -> FidvrConfig:
    """Configure the WECC Motor D FIDVR experiment."""
    unsupported_motor_model = os.environ.get("FIDVR_MOTOR_MODEL", "").strip()
    if unsupported_motor_model and unsupported_motor_model not in {"wecc", "wecc_motor_d"}:
        raise ValueError(
            "FIDVR_MOTOR_MODEL is now WECC-only. Remove the override or use "
            "FIDVR_MOTOR_MODEL=wecc_motor_d."
        )

    thermal_restore_min_delay_s = get_nonnegative_env_float(
        "FIDVR_THERMAL_RESTORE_MIN_DELAY_S", 180.0
    )
    thermal_restore_max_delay_s = get_nonnegative_env_float(
        "FIDVR_THERMAL_RESTORE_MAX_DELAY_S", 300.0
    )
    if thermal_restore_max_delay_s < thermal_restore_min_delay_s:
        raise ValueError(
            "Invalid FIDVR thermal restoration delay window: "
            "FIDVR_THERMAL_RESTORE_MAX_DELAY_S must be >= "
            "FIDVR_THERMAL_RESTORE_MIN_DELAY_S."
        )

    return FidvrConfig(
        enabled=get_env_bool("FIDVR_ENABLE", False),
        motor_loads=get_env_name_list(
            "FIDVR_MOTOR_LOADS",
            "634a,634b,634c,645,675a,675b,675c,611,652,670a,670b,670c",
        ),
        capacitor_names=get_env_name_list("FIDVR_CAPACITORS", "cap1,cap2"),
        regulator_names=get_env_name_list("FIDVR_REGULATORS", "reg1,reg2,reg3"),
        motor_share=get_fraction_env_float("FIDVR_MOTOR_SHARE", 0.35),
        # WECC report map:
        # - Main LD1PAC parameter tables: pp. 66 and 75.
        # - Stall timing tests: pp. 18 and 21.
        # - Contactor, restart, UVR, and thermal tests: pp. 25-41.
        # - Current R/X defaults follow Section 9, p. 58; Appendix 1/3 list
        #   the opposite order. See WECC_MOTOR_D_TRACEABILITY.md.
        wecc_comp_pf=get_positive_env_float("FIDVR_WECC_COMPPF", 0.97),
        wecc_vstall=get_positive_env_float("FIDVR_WECC_VSTALL", 0.70),
        wecc_rstall=get_positive_env_float("FIDVR_WECC_RSTALL", 0.114),
        wecc_xstall=get_positive_env_float("FIDVR_WECC_XSTALL", 0.124),
        wecc_tstall=get_positive_env_float("FIDVR_WECC_TSTALL", 0.033),
        wecc_frst=get_fraction_env_float("FIDVR_WECC_FRST", 0.20),
        wecc_vrst=get_positive_env_float("FIDVR_WECC_VRST", 0.90),
        wecc_trst=get_nonnegative_env_float("FIDVR_WECC_TRST", 0.40),
        wecc_vbrk=get_positive_env_float("FIDVR_WECC_VBRK", 0.86),
        wecc_vc1off=get_positive_env_float("FIDVR_WECC_VC1OFF", 0.45),
        wecc_vc2off=get_positive_env_float("FIDVR_WECC_VC2OFF", 0.35),
        wecc_vc1on=get_positive_env_float("FIDVR_WECC_VC1ON", 0.50),
        wecc_vc2on=get_positive_env_float("FIDVR_WECC_VC2ON", 0.40),
        wecc_tth=get_positive_env_float("FIDVR_WECC_TTH", 10.0),
        wecc_th1t=get_positive_env_float("FIDVR_WECC_TH1T", 1.30),
        wecc_th2t=get_positive_env_float("FIDVR_WECC_TH2T", 4.30),
        wecc_fuvr=get_fraction_env_float("FIDVR_WECC_FUVR", 0.0),
        wecc_uvtr1=get_positive_env_float("FIDVR_WECC_UVTR1", 0.80),
        wecc_ttr1=get_nonnegative_env_float("FIDVR_WECC_TTR1", 0.20),
        wecc_uvtr2=get_positive_env_float("FIDVR_WECC_UVTR2", 0.90),
        wecc_ttr2=get_nonnegative_env_float("FIDVR_WECC_TTR2", 5.0),
        enable_thermal_load_restoration=get_env_bool(
            "FIDVR_ENABLE_THERMAL_LOAD_RESTORATION", False
        ),
        thermal_restore_min_delay_s=thermal_restore_min_delay_s,
        thermal_restore_max_delay_s=thermal_restore_max_delay_s,
        thermal_restore_ramp_s=get_nonnegative_env_float(
            "FIDVR_THERMAL_RESTORE_RAMP_S", 30.0
        ),
        thermal_restore_voltage_pu=get_positive_env_float(
            "FIDVR_THERMAL_RESTORE_VOLTAGE_PU", 0.90
        ),
        thermal_restore_dropout_voltage_pu=get_positive_env_float(
            "FIDVR_THERMAL_RESTORE_DROPOUT_VOLTAGE_PU", 0.70
        ),
        thermal_restore_fraction=get_fraction_env_float(
            "FIDVR_THERMAL_RESTORE_FRACTION", 1.0
        ),
        regulator_low_voltage_pu=get_positive_env_float(
            "FIDVR_REGULATOR_LOW_VOLTAGE_PU", 0.99
        ),
        regulator_high_voltage_pu=get_positive_env_float(
            "FIDVR_REGULATOR_HIGH_VOLTAGE_PU", 1.03
        ),
        regulator_monitor_bus=os.environ.get("FIDVR_REGULATOR_MONITOR_BUS", "").strip(),
        regulator_delay_s=get_positive_env_float("FIDVR_REGULATOR_DELAY_S", 15.0),
        regulator_tap_delay_s=get_positive_env_float(
            "FIDVR_REGULATOR_TAP_DELAY_S", 2.0
        ),
        capacitor_on_voltage_pu=get_positive_env_float(
            "FIDVR_CAPACITOR_ON_VOLTAGE_PU", 0.97
        ),
        capacitor_off_voltage_pu=get_positive_env_float(
            "FIDVR_CAPACITOR_OFF_VOLTAGE_PU", 1.03
        ),
        capacitor_on_delay_s=get_positive_env_float(
            "FIDVR_CAPACITOR_ON_DELAY_S", 10.0
        ),
        capacitor_off_delay_s=get_positive_env_float(
            "FIDVR_CAPACITOR_OFF_DELAY_S", 2.0
        ),
        initial_capacitor_fraction=get_fraction_env_float(
            "FIDVR_CAPACITOR_INITIAL_FRACTION", 1.0
        ),
        capacitor_lockout_after_open=get_env_bool(
            "FIDVR_CAPACITOR_LOCKOUT_AFTER_OPEN", False
        ),
        capacitor_kvar_scale=get_positive_env_float(
            "FIDVR_CAPACITOR_KVAR_SCALE", 1.0
        ),
        enable_reg_control=get_env_bool("FIDVR_ENABLE_REG_CONTROL", False),
        enable_cap_control=get_env_bool("FIDVR_ENABLE_CAP_CONTROL", False),
        alert_signal=get_env_choice(
            "FIDVR_ALERT_SIGNAL", "dist_bus", {"dist_bus", "source", "bus"}
        ),
        alert_bus=os.environ.get("FIDVR_ALERT_BUS", "").strip(),
        prefault_min_voltage_pu=get_positive_env_float(
            "FIDVR_PREFAULT_MIN_VOLTAGE_PU", 0.95
        ),
    )


# =============================================================================
# Shared Numeric and Naming Helpers
# =============================================================================

def _phase_value(phase_map, phase: int) -> float:
    return phase_map.get(phase, math.nan)


def _complex_from_polar(magnitude: float, angle_deg: float) -> complex:
    angle_rad = math.radians(angle_deg)
    return complex(magnitude * math.cos(angle_rad), magnitude * math.sin(angle_rad))


def _safe_mean(values: list[float]) -> float:
    finite_values = [value for value in values if math.isfinite(value)]
    if not finite_values:
        return math.nan
    return sum(finite_values) / len(finite_values)


def _metric_token(name: str) -> str:
    token = "".join(ch if ch.isalnum() else "_" for ch in name.strip().lower())
    return token.strip("_") or "unnamed"


def _deterministic_unit_interval(key: str) -> float:
    accumulator = 0
    for idx, char in enumerate(key):
        accumulator = (accumulator * 131 + (idx + 17) * ord(char)) % 104729
    return accumulator / 104729.0


def _all_finite(values) -> bool:
    return all(math.isfinite(value) for value in values)


def _alert_voltage_from_snapshot(dist_bus_snapshot: dict) -> float:
    vpos = float(dist_bus_snapshot.get("positive_seq_mag", math.nan))
    if math.isfinite(vpos):
        return vpos
    return float(dist_bus_snapshot.get("avg_mag", math.nan))


def _alert_voltage_from_bus_snapshot(bus_name: str, bus_snapshot: dict) -> float:
    phases = _bus_phases(bus_name)
    if phases:
        phase_values = [
            bus_snapshot["phase_mags"].get(phase, math.nan)
            for phase in phases
            if phase in bus_snapshot["phase_mags"]
        ]
        return _safe_mean(phase_values)
    return _alert_voltage_from_snapshot(bus_snapshot)


def _resolve_alert_bus(fidvr: FidvrConfig, dist_voltage_bus: str) -> str:
    if fidvr.alert_signal == "bus":
        return fidvr.alert_bus or dist_voltage_bus
    return dist_voltage_bus


def _alert_label_from_snapshot(mode: str, bus_name: str, bus_snapshot: dict) -> str:
    if mode == "source":
        return "Interface source |V|"
    if _bus_phases(bus_name):
        return f"{bus_name} |V|"
    if math.isfinite(float(bus_snapshot.get("positive_seq_mag", math.nan))):
        return f"{bus_name} |V1|"
    return f"{bus_name} avg |V|"


def build_alert_signal_info(
    fidvr: FidvrConfig,
    dist_voltage_bus: str,
    dist_bus_snapshot: dict,
    source_v_pu: float,
) -> dict[str, object]:
    if fidvr.alert_signal == "source":
        return {
            "alert_signal_mode": "source",
            "alert_bus": "source",
            "alert_v_pu": source_v_pu,
            "alert_vpos_pu": source_v_pu,
            "alert_vavg_pu": source_v_pu,
            "alert_label": "Interface source |V|",
        }

    alert_bus = _resolve_alert_bus(fidvr, dist_voltage_bus)
    if _bus_base_name(alert_bus).lower() == dist_bus_snapshot["bus"].lower():
        alert_bus_snapshot = dist_bus_snapshot
    else:
        alert_bus_snapshot = get_bus_voltage_snapshot(_bus_base_name(alert_bus))

    return {
        "alert_signal_mode": fidvr.alert_signal,
        "alert_bus": alert_bus,
        "alert_v_pu": _alert_voltage_from_bus_snapshot(alert_bus, alert_bus_snapshot),
        "alert_vpos_pu": float(alert_bus_snapshot.get("positive_seq_mag", math.nan)),
        "alert_vavg_pu": float(alert_bus_snapshot.get("avg_mag", math.nan)),
        "alert_label": _alert_label_from_snapshot(
            fidvr.alert_signal, alert_bus, alert_bus_snapshot
        ),
    }


def _sequence_magnitudes(phase_mags: dict[int, float], phase_angles: dict[int, float]) -> dict:
    if not all(phase in phase_mags and phase in phase_angles for phase in (1, 2, 3)):
        return {
            "positive_seq_mag": math.nan,
            "negative_seq_mag": math.nan,
            "zero_seq_mag": math.nan,
        }

    va = _complex_from_polar(phase_mags[1], phase_angles[1])
    vb = _complex_from_polar(phase_mags[2], phase_angles[2])
    vc = _complex_from_polar(phase_mags[3], phase_angles[3])
    a = complex(-0.5, math.sqrt(3.0) / 2.0)
    a2 = complex(-0.5, -math.sqrt(3.0) / 2.0)

    v0 = (va + vb + vc) / 3.0
    v1 = (va + a * vb + a2 * vc) / 3.0
    v2 = (va + a2 * vb + a * vc) / 3.0
    return {
        "positive_seq_mag": abs(v1),
        "negative_seq_mag": abs(v2),
        "zero_seq_mag": abs(v0),
    }


def get_bus_voltage_snapshot(bus_name: str) -> dict:
    dss.Circuit.SetActiveBus(bus_name)
    active_bus = dss.Bus.Name()
    if active_bus.lower() != bus_name.lower():
        raise RuntimeError(
            f"Requested distribution voltage bus '{bus_name}' but OpenDSS activated "
            f"'{active_bus}'."
        )

    pu_mag_angle = dss.Bus.puVmagAngle()
    nodes = dss.Bus.Nodes()

    phase_mags = {}
    phase_angles = {}
    for idx, node in enumerate(nodes):
        mag_idx = 2 * idx
        ang_idx = mag_idx + 1
        if ang_idx >= len(pu_mag_angle):
            continue
        phase_mags[node] = pu_mag_angle[mag_idx]
        phase_angles[node] = pu_mag_angle[ang_idx]

    present_phase_mags = [phase_mags[node] for node in sorted(phase_mags)]
    avg_mag = (
        sum(present_phase_mags) / len(present_phase_mags)
        if present_phase_mags
        else math.nan
    )

    return {
        "bus": active_bus,
        "avg_mag": avg_mag,
        "phase_mags": phase_mags,
        "phase_angles": phase_angles,
        **_sequence_magnitudes(phase_mags, phase_angles),
    }


def initialize_all_bus_voltage_csv(csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        csv.DictWriter(handle, fieldnames=ALL_BUS_VOLTAGE_COLUMNS).writeheader()


def append_all_bus_voltage_rows(
    csv_path: Path,
    feeder_idx: int,
    iter_count: int,
    current_time: float,
    state: str,
    bus_names: tuple[str, ...],
) -> None:
    if not bus_names:
        return

    rows = []
    for bus_name in bus_names:
        try:
            snapshot = get_bus_voltage_snapshot(bus_name)
        except RuntimeError:
            continue
        rows.append(
            {
                "feeder": feeder_idx,
                "iter": iter_count,
                "t_granted": current_time,
                "state": state,
                "bus": snapshot["bus"],
                "node_count": len(snapshot["phase_mags"]),
                "vavg_pu": snapshot["avg_mag"],
                "vpos_pu": snapshot["positive_seq_mag"],
                "vneg_pu": snapshot["negative_seq_mag"],
                "vzero_pu": snapshot["zero_seq_mag"],
                "va_pu": _phase_value(snapshot["phase_mags"], 1),
                "vb_pu": _phase_value(snapshot["phase_mags"], 2),
                "vc_pu": _phase_value(snapshot["phase_mags"], 3),
                "anga_deg": _phase_value(snapshot["phase_angles"], 1),
                "angb_deg": _phase_value(snapshot["phase_angles"], 2),
                "angc_deg": _phase_value(snapshot["phase_angles"], 3),
            }
        )

    if not rows:
        return

    with csv_path.open("a", newline="", encoding="utf-8") as handle:
        csv.DictWriter(handle, fieldnames=ALL_BUS_VOLTAGE_COLUMNS).writerows(rows)


# =============================================================================
# OpenDSS Case Introspection and Device Editing, the bridge between Python and the OpenDSS feeder model.
# =============================================================================

def collect_load_specs(load_scale: float) -> dict[str, LoadSpec]:
    specs = {}
    for name in dss.Loads.AllNames():
        dss.Loads.Name(name)
        specs[name.lower()] = LoadSpec(
            name=name.lower(),
            kw=float(dss.Loads.kW()) * load_scale,
            kvar=float(dss.Loads.kvar()) * load_scale,
            bus=str(dss.CktElement.BusNames()[0]),
            kv=float(dss.Loads.kV()),
            phases=int(dss.Loads.Phases()),
            conn="delta" if dss.Loads.IsDelta() else "wye",
        )
    return specs


def _phase_from_bus_name(bus_name: str) -> int:
    parts = bus_name.split(".")
    if len(parts) >= 2:
        try:
            return int(parts[1])
        except ValueError:
            pass
    return 1


def _bus_base_name(bus_name: str) -> str:
    return bus_name.split(".")[0]


def _bus_phases(bus_name: str) -> tuple[int, ...]:
    phases = []
    for token in bus_name.split(".")[1:]:
        try:
            phases.append(int(token))
        except ValueError:
            continue
    return tuple(phases)


def get_monitored_bus_voltage_pu(bus_name: str, prefer_positive_sequence: bool = False) -> float:
    snapshot = get_bus_voltage_snapshot(_bus_base_name(bus_name))
    phases = _bus_phases(bus_name)
    if phases:
        phase_values = [
            snapshot["phase_mags"].get(phase, math.nan)
            for phase in phases
            if phase in snapshot["phase_mags"]
        ]
        return _safe_mean(phase_values)
    if prefer_positive_sequence and math.isfinite(snapshot["positive_seq_mag"]):
        return snapshot["positive_seq_mag"]
    return snapshot["avg_mag"]


def collect_regulator_specs(regulator_names: tuple[str, ...]) -> dict[str, RegulatorSpec]:
    specs = {}
    available = {name.lower() for name in dss.Transformers.AllNames()}
    for name in regulator_names:
        if name not in available:
            raise RuntimeError(
                f"FIDVR regulator '{name}' was not found in the OpenDSS case."
            )
        dss.Transformers.Name(name)
        dss.Transformers.Wdg(2)
        bus_names = list(dss.CktElement.BusNames())
        tap = float(dss.Transformers.Tap())
        min_tap = float(dss.Transformers.MinTap())
        max_tap = float(dss.Transformers.MaxTap())
        num_taps = max(int(dss.Transformers.NumTaps()), 1)
        specs[name] = RegulatorSpec(
            name=name,
            baseline_tap=tap,
            min_tap=min_tap,
            max_tap=max_tap,
            tap_step=(max_tap - min_tap) / num_taps,
            monitor_bus=bus_names[1],
            phase=_phase_from_bus_name(bus_names[1]),
        )
    return specs


def collect_capacitor_specs(
    capacitor_names: tuple[str, ...],
    kvar_scale: float,
) -> dict[str, CapacitorSpec]:
    specs = {}
    available = {name.lower() for name in dss.Capacitors.AllNames()}
    for name in capacitor_names:
        if name not in available:
            raise RuntimeError(
                f"FIDVR capacitor '{name}' was not found in the OpenDSS case."
            )
        dss.Capacitors.Name(name)
        base_kvar = float(dss.Capacitors.kvar())
        applied_kvar = base_kvar * kvar_scale
        if abs(kvar_scale - 1.0) > 1e-9:
            dss.Text.Command(f"Edit Capacitor.{name} kvar={applied_kvar:.6f}")
            dss.Capacitors.Name(name)
            print(
                f"Feeder FIDVR capacitor aggregation: Capacitor.{name} "
                f"kvar {base_kvar:.3f} -> {applied_kvar:.3f} "
                f"(scale={kvar_scale:.3f})"
            )
        dss.Circuit.SetActiveElement(f"Capacitor.{name}")
        bus_name = str(dss.CktElement.BusNames()[0])
        specs[name] = CapacitorSpec(
            name=name,
            bus=bus_name,
            phases=int(dss.CktElement.NumPhases()),
            kv=float(dss.Capacitors.kV()),
            base_kvar=base_kvar,
            applied_kvar=applied_kvar,
        )
    return specs


def _apply_baseline_loads(load_specs: dict[str, LoadSpec]) -> None:
    for spec in load_specs.values():
        dss.Text.Command(
            f"Edit Load.{spec.name} kW={spec.kw:.6f} kvar={spec.kvar:.6f}"
        )


def _set_capacitor_fraction(capacitor_names: tuple[str, ...], fraction: float) -> None:
    if not capacitor_names:
        return

    clipped_fraction = max(0.0, min(1.0, fraction))
    enabled_count = int(round(clipped_fraction * len(capacitor_names)))
    enabled_count = max(0, min(len(capacitor_names), enabled_count))

    for idx, name in enumerate(capacitor_names):
        _set_enabled(f"Capacitor.{name}", idx < enabled_count)


def _initialize_capacitor_states(
    capacitor_specs: dict[str, CapacitorSpec],
    runtime_state: FeederRuntimeState,
    fraction: float,
) -> None:
    ordered_specs = list(capacitor_specs.values())
    if not ordered_specs:
        return

    clipped_fraction = max(0.0, min(1.0, fraction))
    enabled_count = int(round(clipped_fraction * len(ordered_specs)))
    enabled_count = max(0, min(len(ordered_specs), enabled_count))

    for idx, spec in enumerate(ordered_specs):
        is_enabled = idx < enabled_count
        runtime_state.capacitor_states[spec.name] = is_enabled
        runtime_state.capacitor_on_armed_since[spec.name] = None
        runtime_state.capacitor_off_armed_since[spec.name] = None
        runtime_state.capacitor_locked_out[spec.name] = False
        runtime_state.capacitor_last_action[spec.name] = ""
        runtime_state.capacitor_monitored_voltage[spec.name] = math.nan
        _set_enabled(f"Capacitor.{spec.name}", is_enabled)


def _set_enabled(element_name: str, enabled: bool) -> None:
    action = "Enable" if enabled else "Disable"
    dss.Text.Command(f"{action} {element_name}")


def _clip_tap(value: float, reg_spec: RegulatorSpec) -> float:
    return max(reg_spec.min_tap, min(reg_spec.max_tap, value))


def _set_regulator_taps(
    regulator_specs: dict[str, RegulatorSpec],
    tap_offset: float,
) -> float:
    applied_taps = []
    for spec in regulator_specs.values():
        tap_value = _clip_tap(spec.baseline_tap + tap_offset, spec)
        dss.Text.Command(f"Transformer.{spec.name}.Taps=[1.0 {tap_value:.5f}]")
        applied_taps.append(tap_value)
    if not applied_taps:
        return math.nan
    return sum(applied_taps) / len(applied_taps)


# =============================================================================
# WECC Motor D FIDVR Model
# =============================================================================
# Equation for the local WECC A/C motor report:
# - Baseline PF and load partitioning: pp. 58, 66-67.
# - Running state equations: p. 68.
# - Stalled constant-impedance equations: pp. 68-69.
# - Stall parameter calculations and validation: pp. 70-73.
# Detailed traceability is in WECC_MOTOR_D_TRACEABILITY.md.

def _wecc_motor_baseline_kvar(motor_kw: float, fidvr: FidvrConfig) -> float:
    comp_pf = max(1e-6, min(0.999999, fidvr.wecc_comp_pf))
    return motor_kw * math.tan(math.acos(comp_pf))


def _wecc_motor_d_stall_pq_pu(v_pu: float, fidvr: FidvrConfig) -> tuple[float, float]:
    v = max(0.0, v_pu)
    impedance_sq = max(1e-9, fidvr.wecc_rstall**2 + fidvr.wecc_xstall**2)
    gstall = fidvr.wecc_rstall / impedance_sq
    bstall = fidvr.wecc_xstall / impedance_sq
    return gstall * v * v, bstall * v * v


def _wecc_motor_d_vstall_break(fidvr: FidvrConfig) -> float:
    vbrk = fidvr.wecc_vbrk
    vstall = fidvr.wecc_vstall
    if vstall <= 0.4:
        return vstall

    v = 0.4
    while v < vstall:
        p_stall, _ = _wecc_motor_d_stall_pq_pu(v, fidvr)
        p_running = 1.0 + 12.0 * max(0.0, vbrk - v) ** 3.2
        if p_running <= p_stall:
            return v
        v += 0.0001
    return vstall


def _wecc_motor_d_running_pq_pu(v_pu: float, fidvr: FidvrConfig) -> tuple[float, float]:
    v = max(0.0, v_pu)
    vbrk = fidvr.wecc_vbrk
    comp_pf = max(1e-6, min(0.999999, fidvr.wecc_comp_pf))
    q0 = math.tan(math.acos(comp_pf)) - 6.0 * max(0.0, 1.0 - vbrk) ** 2.0

    # If the voltage is higher than the stalling voltage break point, the motor is running at full load with a power factor that depends on the voltage.
    if v > vbrk:
        return 1.0, max(0.0, q0 + 6.0 * (v - vbrk) ** 2.0)

    # If the voltage is lower than the stalling voltage break point, the motor is running at another state.
    if v > _wecc_motor_d_vstall_break(fidvr):
        return (
            1.0 + 12.0 * (vbrk - v) ** 3.2,
            max(0.0, q0 + 11.0 * (vbrk - v) ** 2.5),
        )

    return _wecc_motor_d_stall_pq_pu(v, fidvr)


def build_wecc_motor_d_motors(
    load_specs: dict[str, LoadSpec],
    fidvr: FidvrConfig,
) -> tuple[MotorElementSpec, ...]:
    if not fidvr.enabled:
        return ()

    missing_motor_loads = [name for name in fidvr.motor_loads if name not in load_specs]
    if missing_motor_loads:
        raise RuntimeError(
            "FIDVR motor loads were not found in the OpenDSS case: "
            + ", ".join(missing_motor_loads)
        )

    motor_specs = []
    skipped_multiphase = []
    for motor_index, load_name in enumerate(fidvr.motor_loads):
        spec = load_specs[load_name]
        if spec.phases != 1:
            skipped_multiphase.append(spec.name)
            continue

        motor_kw = spec.kw * fidvr.motor_share
        if motor_kw <= 1e-9:
            continue

        baseline_kvar = _wecc_motor_baseline_kvar(motor_kw, fidvr)
        static_kw = max(0.0, spec.kw - motor_kw)
        static_kvar = spec.kvar - baseline_kvar
        dss.Text.Command(
            f"Edit Load.{spec.name} kW={static_kw:.6f} kvar={static_kvar:.6f}"
        )

        kva = max(1e-3, motor_kw / max(1e-6, min(0.999999, fidvr.wecc_comp_pf)))
        motor_name = f"weccmd_{spec.name}"
        stall_pu, stall_q_pu = _wecc_motor_d_stall_pq_pu(
            fidvr.wecc_vstall, fidvr
        )
        phase = _phase_from_bus_name(spec.bus)

        dss.Text.Command(
            " ".join(
                [
                    f"New Load.{motor_name}",
                    f"phases={spec.phases}",
                    f"bus1={spec.bus}",
                    f"conn={spec.conn}",
                    f"kv={spec.kv:.6f}",
                    f"kW={motor_kw:.6f}",
                    f"kvar={baseline_kvar:.6f}",
                    "model=1",
                    "status=variable",
                    "vminpu=0.00",
                    "vmaxpu=2.00",
                ]
            )
        )
        motor_specs.append(
            MotorElementSpec(
                element_name=f"Load.{motor_name}",
                source_load_name=spec.name,
                group_index=len(motor_specs),
                phase=phase,
                phases=spec.phases,
                kw=motor_kw,
                kva=kva,
                baseline_kvar=baseline_kvar,
                stall_kw=max(1e-3, motor_kw * stall_pu),
                stall_kvar=max(1e-3, motor_kw * stall_q_pu),
                bus=spec.bus,
                kv=spec.kv,
                conn=spec.conn,
            )
        )

    if skipped_multiphase:
        print(
            "Feeder compressor motors: skipped non-single-phase loads for "
            "WECC Motor D conversion: "
            f"{', '.join(skipped_multiphase)}"
        )

    return tuple(motor_specs)


def build_motor_elements(
    load_specs: dict[str, LoadSpec],
    fidvr: FidvrConfig,
) -> tuple[MotorElementSpec, ...]:
    return build_wecc_motor_d_motors(load_specs, fidvr)


# =============================================================================
# FIDVR State Summaries and Health Checks
# =============================================================================

def collect_motor_diagnostics(runtime_state: FeederRuntimeState) -> dict:
    return {
        "motor_slip_avg": math.nan,
        "motor_slip_max": math.nan,
        "motor_pf_avg": math.nan,
    }


def collect_motor_control_summary(runtime_state: FeederRuntimeState) -> dict:
    if not runtime_state.motor_elements:
        return {}

    p_scales = []
    q_scales = []
    restore_fracs = []
    thermal_restore_fracs = []
    mode_counts = {"running": 0, "stalled": 0, "tripped": 0, "restoring": 0}
    trip_reason_counts = {"contactor": 0, "thermal": 0, "locked_out": 0}
    thermal_restoring_groups = 0
    thermal_restored_groups = 0

    for motor in runtime_state.motor_elements:
        mode = runtime_state.motor_group_states.get(motor.element_name, "running")
        if mode not in mode_counts:
            mode = "restoring"
        mode_counts[mode] += 1
        trip_reason = runtime_state.motor_trip_reason.get(motor.element_name, "")
        if trip_reason == "contactor":
            trip_reason_counts["contactor"] += 1
        elif trip_reason == "thermal":
            trip_reason_counts["thermal"] += 1
            if mode == "tripped" and runtime_state.motor_wecc_a_stalled.get(
                motor.element_name, False
            ):
                trip_reason_counts["locked_out"] += 1
        p_scales.append(runtime_state.motor_group_p_scales.get(motor.element_name, 1.0))
        q_scales.append(runtime_state.motor_group_q_scales.get(motor.element_name, 1.0))
        restore_fracs.append(runtime_state.motor_restore_frac.get(motor.element_name, 1.0))
        thermal_restore_frac = runtime_state.motor_thermal_restore_frac.get(
            motor.element_name, 0.0
        )
        thermal_restore_target = runtime_state.motor_thermal_restore_target.get(
            motor.element_name, 0.0
        )
        thermal_restore_fracs.append(thermal_restore_frac)
        if thermal_restore_frac > 0.02 and thermal_restore_frac < thermal_restore_target - 0.02:
            thermal_restoring_groups += 1
        elif thermal_restore_target > 0.02 and thermal_restore_frac >= thermal_restore_target - 0.02:
            thermal_restored_groups += 1

    return {
        "motor_p_scale": _safe_mean(p_scales),
        "motor_q_scale": _safe_mean(q_scales),
        "motor_restore_frac": _safe_mean(restore_fracs),
        "motor_thermal_restore_frac": _safe_mean(thermal_restore_fracs),
        "motor_running_groups": mode_counts["running"],
        "motor_stalled_groups": mode_counts["stalled"],
        "motor_tripped_groups": mode_counts["tripped"],
        "motor_restoring_groups": mode_counts["restoring"],
        "motor_thermal_restoring_groups": thermal_restoring_groups,
        "motor_thermal_restored_groups": thermal_restored_groups,
        "motor_contactor_open_groups": trip_reason_counts["contactor"],
        "motor_thermal_trip_groups": trip_reason_counts["thermal"],
        "motor_locked_out_groups": trip_reason_counts["locked_out"],
    }


def log_prefault_voltage_health(
    feeder_idx: int,
    fidvr: FidvrConfig,
    dist_bus_snapshot: dict,
    alert_signal_info: dict[str, object],
    source_v_pu: float,
) -> None:
    dist_v_pu = float(
        dist_bus_snapshot.get(
            "positive_seq_mag", float(dist_bus_snapshot.get("avg_mag", math.nan))
        )
    )
    alert_v_pu = float(alert_signal_info["alert_v_pu"])
    minimum = fidvr.prefault_min_voltage_pu

    print(
        f"Feeder {feeder_idx}: pre-fault health "
        f"source_v={source_v_pu:.6f} pu "
        f"dist_bus={dist_bus_snapshot['bus']} dist_v={dist_v_pu:.6f} pu "
        f"alert={alert_signal_info['alert_label']} alert_v={alert_v_pu:.6f} pu "
        f"min_expected={minimum:.3f} pu"
    )

    warnings = []
    if math.isfinite(source_v_pu) and source_v_pu < minimum:
        warnings.append(f"source {source_v_pu:.3f} pu")
    if math.isfinite(dist_v_pu) and dist_v_pu < minimum:
        warnings.append(f"{dist_bus_snapshot['bus']} {dist_v_pu:.3f} pu")
    if math.isfinite(alert_v_pu) and alert_v_pu < minimum:
        warnings.append(
            f"{alert_signal_info['alert_label']} {alert_v_pu:.3f} pu"
        )
    if warnings:
        print(
            f"[Feeder{feeder_idx:02d} PREFLIGHT WARN] "
            "Pre-fault voltage is already depressed at "
            + ", ".join(warnings)
            + ". This case is behaving more like a stressed-feeder demonstrator "
            "than a healthy pre-contingency calibration."
        )


def collect_regulator_tap_summary(regulator_specs: dict[str, RegulatorSpec]) -> dict:
    if not regulator_specs:
        return {"reg_tap_avg": math.nan}

    tap_values = []
    summary = {}
    for name in sorted(regulator_specs):
        dss.Transformers.Name(name)
        dss.Transformers.Wdg(2)
        tap = float(dss.Transformers.Tap())
        tap_values.append(tap)
        summary[f"reg_tap_{_metric_token(name)}"] = tap
    summary["reg_tap_avg"] = _safe_mean(tap_values)
    return summary


# =============================================================================
# FIDVR Timeline, Motor Updates, and Feeder Controls
# =============================================================================

def get_fidvr_timeline(disturbance: DisturbanceConfig, fidvr: FidvrConfig) -> dict:
    if not disturbance.enabled:
        trigger_time = get_positive_env_float("FIDVR_TRIGGER_TIME", 1.0)
        clear_time = trigger_time + get_positive_env_float("FIDVR_FAULT_DURATION", 0.20)
    else:
        trigger_time = disturbance.fault_time
        clear_time = disturbance.clear_time

    return {
        "trigger_time": trigger_time,
        "clear_time": clear_time,
    }


def _average_baseline_tap(regulator_specs: dict[str, RegulatorSpec]) -> float:
    if not regulator_specs:
        return math.nan
    return sum(spec.baseline_tap for spec in regulator_specs.values()) / len(
        regulator_specs
    )


def get_capacitor_fraction(
    runtime_state: FeederRuntimeState,
    capacitor_specs: dict[str, CapacitorSpec],
) -> float:
    if not capacitor_specs:
        return 1.0
    enabled = sum(1 for spec in capacitor_specs.values() if runtime_state.capacitor_states.get(spec.name, True))
    return enabled / len(capacitor_specs)


def apply_motor_group_targets(
    runtime_state: FeederRuntimeState,
    motor: MotorElementSpec,
    group_mode: str,
    target_kw: float,
    target_kvar: float,
) -> None:
    target_kw = max(1e-3, target_kw)
    target_kvar = max(1e-3, target_kvar)

    dss.Text.Command(
        f"Edit {motor.element_name} kW={target_kw:.6f} kvar={target_kvar:.6f}"
    )
    runtime_state.motor_group_states[motor.element_name] = group_mode
    runtime_state.motor_group_p_scales[motor.element_name] = target_kw / max(motor.kw, 1e-6)
    runtime_state.motor_group_q_scales[motor.element_name] = target_kvar / max(
        motor.baseline_kvar, 1e-6
    )


def _clip_fraction(value: float) -> float:
    return max(0.0, min(1.0, value))


def _wecc_contactor_fraction(previous: float, v_motor: float, fidvr: FidvrConfig) -> float:
    previous = _clip_fraction(previous)
    off_span = max(1e-6, fidvr.wecc_vc1off - fidvr.wecc_vc2off)
    on_span = max(1e-6, fidvr.wecc_vc1on - fidvr.wecc_vc2on)
    off_curve = _clip_fraction((v_motor - fidvr.wecc_vc2off) / off_span)
    on_curve = _clip_fraction((v_motor - fidvr.wecc_vc2on) / on_span)

    if v_motor < fidvr.wecc_vc1off:
        return min(previous, off_curve)
    if v_motor > fidvr.wecc_vc2on:
        return max(previous, on_curve)
    return previous


def _wecc_thermal_fraction(theta: float, fidvr: FidvrConfig) -> float:
    if theta <= fidvr.wecc_th1t:
        return 1.0
    if theta >= fidvr.wecc_th2t:
        return 0.0
    return _clip_fraction(
        (fidvr.wecc_th2t - theta)
        / max(1e-6, fidvr.wecc_th2t - fidvr.wecc_th1t)
    )


def _wecc_update_temperature(
    theta: float,
    stalled: bool,
    v_motor: float,
    fidvr: FidvrConfig,
    dt: float,
) -> float:
    if stalled:
        i2r, _ = _wecc_motor_d_stall_pq_pu(v_motor, fidvr)
        target = i2r
    else:
        target = 0.0
    if dt <= 0.0:
        return max(0.0, theta)
    alpha = min(1.0, dt / max(1e-6, fidvr.wecc_tth))
    return max(0.0, theta + alpha * (target - theta))


def _thermal_restore_delay_s(element: str, fidvr: FidvrConfig) -> float:
    span = fidvr.thermal_restore_max_delay_s - fidvr.thermal_restore_min_delay_s
    if span <= 1e-9:
        return fidvr.thermal_restore_min_delay_s
    return (
        fidvr.thermal_restore_min_delay_s
        + span * _deterministic_unit_interval(f"thermal_restore:{element}")
    )


def _update_wecc_thermal_load_restoration(
    runtime_state: FeederRuntimeState,
    fidvr: FidvrConfig,
    element: str,
    thermal_trip_fraction: float,
    v_motor: float,
    current_time: float,
) -> float:
    if not fidvr.enable_thermal_load_restoration:
        runtime_state.motor_thermal_restore_trip_time[element] = None
        runtime_state.motor_thermal_restore_started_at[element] = None
        runtime_state.motor_thermal_restore_frac[element] = 0.0
        runtime_state.motor_thermal_restore_target[element] = 0.0
        return 0.0

    delay_s = runtime_state.motor_thermal_restore_delay_s.get(element)
    if delay_s is None or not math.isfinite(delay_s):
        delay_s = _thermal_restore_delay_s(element, fidvr)
        runtime_state.motor_thermal_restore_delay_s[element] = delay_s

    state = ThermalLoadRestorationState(
        trip_time_s=runtime_state.motor_thermal_restore_trip_time.get(element),
        restore_started_at_s=runtime_state.motor_thermal_restore_started_at.get(element),
        restored_fraction=runtime_state.motor_thermal_restore_frac.get(element, 0.0),
        target_fraction=runtime_state.motor_thermal_restore_target.get(element, 0.0),
    )
    next_state = update_thermal_load_restoration(
        state,
        ThermalLoadRestorationConfig(
            enabled=True,
            restore_delay_s=delay_s,
            restore_ramp_s=fidvr.thermal_restore_ramp_s,
            restore_voltage_pu=fidvr.thermal_restore_voltage_pu,
            dropout_voltage_pu=fidvr.thermal_restore_dropout_voltage_pu,
            restore_fraction=fidvr.thermal_restore_fraction,
        ),
        thermal_trip_fraction=thermal_trip_fraction,
        voltage_pu=v_motor,
        current_time_s=current_time,
    )
    runtime_state.motor_thermal_restore_trip_time[element] = next_state.trip_time_s
    runtime_state.motor_thermal_restore_started_at[element] = (
        next_state.restore_started_at_s
    )
    runtime_state.motor_thermal_restore_frac[element] = next_state.restored_fraction
    runtime_state.motor_thermal_restore_target[element] = next_state.target_fraction
    return next_state.restored_fraction


def _update_wecc_motor_d_state(
    runtime_state: FeederRuntimeState,
    fidvr: FidvrConfig,
    motor: MotorElementSpec,
    v_motor: float,
    current_time: float,
    dt: float,
) -> tuple[str, float, float, float]:
    """Advance one Motor D group by one co-simulation/control interval."""

    element = motor.element_name
    frst = _clip_fraction(fidvr.wecc_frst)
    frac_a = 1.0 - frst
    frac_b = frst

    a_stalled = runtime_state.motor_wecc_a_stalled.get(element, False)
    b_stalled = runtime_state.motor_wecc_b_stalled.get(element, False)
    b_restarted = runtime_state.motor_wecc_b_restarted.get(element, False)
    stall_armed_since = runtime_state.motor_stall_armed_since.get(element)
    reconnect_armed_since = runtime_state.motor_reconnect_armed_since.get(element)

    # Tstall is short in the WECC defaults, so it is sensitive to the
    # co-simulation/control step. FINE_DT must resolve this timer.
    if (not a_stalled or (not b_stalled and not b_restarted)) and v_motor < fidvr.wecc_vstall:
        if stall_armed_since is None:
            stall_armed_since = current_time
        elif current_time - stall_armed_since >= fidvr.wecc_tstall:
            a_stalled = True
            b_stalled = frst > 0.0 and not b_restarted
            stall_armed_since = None
    elif v_motor >= fidvr.wecc_vstall:
        stall_armed_since = None

    if b_stalled and v_motor > fidvr.wecc_vrst:
        if reconnect_armed_since is None:
            reconnect_armed_since = current_time
        elif current_time - reconnect_armed_since >= fidvr.wecc_trst:
            b_stalled = False
            b_restarted = True
            reconnect_armed_since = None
    else:
        reconnect_armed_since = None

    contactor_fraction = _wecc_contactor_fraction(
        runtime_state.motor_contactor_fraction.get(element, 1.0),
        v_motor,
        fidvr,
    )

    uv_trip_fraction = runtime_state.motor_uv_trip_fraction.get(element, 0.0)
    uv1_armed_since = runtime_state.motor_uv1_armed_since.get(element)
    uv2_armed_since = runtime_state.motor_uv2_armed_since.get(element)
    if fidvr.wecc_fuvr > 0.0:
        if v_motor < fidvr.wecc_uvtr1:
            uv1_armed_since = current_time if uv1_armed_since is None else uv1_armed_since
            if current_time - uv1_armed_since >= fidvr.wecc_ttr1:
                uv_trip_fraction = max(uv_trip_fraction, fidvr.wecc_fuvr)
        else:
            uv1_armed_since = None

        if v_motor < fidvr.wecc_uvtr2:
            uv2_armed_since = current_time if uv2_armed_since is None else uv2_armed_since
            if current_time - uv2_armed_since >= fidvr.wecc_ttr2:
                uv_trip_fraction = max(uv_trip_fraction, fidvr.wecc_fuvr)
        else:
            uv2_armed_since = None

    theta_a = _wecc_update_temperature(
        runtime_state.motor_thermal_state_a.get(element, 0.0),
        a_stalled,
        v_motor,
        fidvr,
        dt,
    )
    theta_b = _wecc_update_temperature(
        runtime_state.motor_thermal_state_b.get(element, 0.0),
        b_stalled,
        v_motor,
        fidvr,
        dt,
    )
    kth_a = _wecc_thermal_fraction(theta_a, fidvr)
    kth_b = _wecc_thermal_fraction(theta_b, fidvr)

    run_p, run_q = _wecc_motor_d_running_pq_pu(v_motor, fidvr)
    stall_p, stall_q = _wecc_motor_d_stall_pq_pu(v_motor, fidvr)
    p_a, q_a = (stall_p, stall_q) if a_stalled else (run_p, run_q)
    p_b, q_b = (stall_p, stall_q) if b_stalled else (run_p, run_q)
    kuvr = 1.0 - _clip_fraction(uv_trip_fraction)

    p_pu = contactor_fraction * kuvr * (
        frac_a * kth_a * p_a + frac_b * kth_b * p_b
    )
    q_pu = contactor_fraction * kuvr * (
        frac_a * kth_a * q_a + frac_b * kth_b * q_b
    )

    stalled_fraction = frac_a * float(a_stalled) * kth_a + frac_b * float(b_stalled) * kth_b
    thermal_trip_fraction = frac_a * (1.0 - kth_a) + frac_b * (1.0 - kth_b)
    disconnected_fraction = 1.0 - contactor_fraction * kuvr
    thermal_restore_frac = _update_wecc_thermal_load_restoration(
        runtime_state,
        fidvr,
        element,
        thermal_trip_fraction,
        v_motor,
        current_time,
    )
    thermal_restore_frac = min(thermal_restore_frac, thermal_trip_fraction)
    if thermal_restore_frac > 1e-9:
        p_pu += contactor_fraction * kuvr * thermal_restore_frac * run_p
        q_pu += contactor_fraction * kuvr * thermal_restore_frac * run_q

    restore_frac = _clip_fraction(
        contactor_fraction
        * kuvr
        * (
            frac_a * (1.0 - float(a_stalled))
            + frac_b * (1.0 - float(b_stalled))
            + thermal_restore_frac
        )
    )
    thermal_restore_target = runtime_state.motor_thermal_restore_target.get(element, 0.0)
    thermal_restore_active = thermal_restore_frac > 0.02
    thermal_restore_complete = (
        thermal_restore_target > 0.02
        and thermal_restore_frac >= thermal_restore_target - 0.02
    )
    if thermal_restore_active and not thermal_restore_complete:
        state = "restoring"
    elif thermal_restore_complete and restore_frac >= 0.98:
        state = "running"
    elif disconnected_fraction > 0.02 or thermal_trip_fraction > 0.02:
        state = "tripped"
    elif stalled_fraction > 0.02:
        state = "stalled"
    elif b_restarted:
        state = "restoring"
    else:
        state = "running"

    runtime_state.motor_wecc_a_stalled[element] = a_stalled
    runtime_state.motor_wecc_b_stalled[element] = b_stalled
    runtime_state.motor_wecc_b_restarted[element] = b_restarted
    runtime_state.motor_stall_armed_since[element] = stall_armed_since
    runtime_state.motor_reconnect_armed_since[element] = reconnect_armed_since
    runtime_state.motor_contactor_fraction[element] = contactor_fraction
    runtime_state.motor_uv_trip_fraction[element] = _clip_fraction(uv_trip_fraction)
    runtime_state.motor_uv1_armed_since[element] = uv1_armed_since
    runtime_state.motor_uv2_armed_since[element] = uv2_armed_since
    runtime_state.motor_thermal_state_a[element] = theta_a
    runtime_state.motor_thermal_state_b[element] = theta_b
    runtime_state.motor_thermal_state[element] = max(theta_a, theta_b)
    runtime_state.motor_trip_reason[element] = (
        "uvr"
        if uv_trip_fraction > 1e-9
        else "contactor"
        if disconnected_fraction > 0.02
        else "thermal"
        if thermal_trip_fraction > 0.02
        else ""
    )
    runtime_state.motor_restore_frac[element] = restore_frac

    return state, max(1e-3, motor.kw * p_pu), max(1e-3, motor.kw * q_pu), restore_frac


def update_motor_group_states(
    runtime_state: FeederRuntimeState,
    fidvr: FidvrConfig,
    current_time: float,
    dt: float,
) -> None:
    for motor in runtime_state.motor_elements:
        v_motor = get_monitored_bus_voltage_pu(motor.bus)
        element = motor.element_name

        state, target_kw, target_kvar, restore_frac = _update_wecc_motor_d_state(
            runtime_state,
            fidvr,
            motor,
            v_motor,
            current_time,
            dt,
        )
        runtime_state.motor_group_states[element] = state
        runtime_state.motor_restore_frac[element] = restore_frac
        apply_motor_group_targets(
            runtime_state,
            motor,
            state,
            target_kw,
            target_kvar,
        )


def update_capacitor_controls(
    capacitor_specs: dict[str, CapacitorSpec],
    fidvr: FidvrConfig,
    runtime_state: FeederRuntimeState,
    current_time: float,
) -> float:
    if not capacitor_specs:
        return 1.0

    for spec in capacitor_specs.values():
        v_cap = get_monitored_bus_voltage_pu(
            spec.bus, prefer_positive_sequence=spec.phases >= 3
        )
        control_state = DelayedShuntControlState(
            enabled=runtime_state.capacitor_states.get(spec.name, True),
            on_armed_since_s=runtime_state.capacitor_on_armed_since.get(spec.name),
            off_armed_since_s=runtime_state.capacitor_off_armed_since.get(spec.name),
            locked_out=runtime_state.capacitor_locked_out.get(spec.name, False),
        )
        control_config = DelayedShuntControlConfig(
            on_voltage_pu=fidvr.capacitor_on_voltage_pu,
            off_voltage_pu=fidvr.capacitor_off_voltage_pu,
            on_delay_s=fidvr.capacitor_on_delay_s,
            off_delay_s=fidvr.capacitor_off_delay_s,
            lockout_after_open=fidvr.capacitor_lockout_after_open,
        )
        updated_state = update_delayed_shunt_control(
            control_state,
            control_config,
            monitored_voltage_pu=v_cap,
            current_time_s=current_time,
        )

        runtime_state.capacitor_states[spec.name] = updated_state.enabled
        runtime_state.capacitor_on_armed_since[spec.name] = (
            updated_state.on_armed_since_s
        )
        runtime_state.capacitor_off_armed_since[spec.name] = (
            updated_state.off_armed_since_s
        )
        runtime_state.capacitor_locked_out[spec.name] = updated_state.locked_out
        runtime_state.capacitor_last_action[spec.name] = updated_state.last_action
        runtime_state.capacitor_monitored_voltage[spec.name] = v_cap
        _set_enabled(f"Capacitor.{spec.name}", updated_state.enabled)

    return get_capacitor_fraction(runtime_state, capacitor_specs)


def update_regulator_controls(
    regulator_specs: dict[str, RegulatorSpec],
    fidvr: FidvrConfig,
    runtime_state: FeederRuntimeState,
    current_time: float,
) -> float:
    if not regulator_specs:
        return math.nan

    applied_taps = []
    for spec in regulator_specs.values():
        monitor_bus = fidvr.regulator_monitor_bus or spec.monitor_bus
        v_mon = get_monitored_bus_voltage_pu(monitor_bus)
        low_armed_since = runtime_state.regulator_low_armed_since.get(spec.name)
        high_armed_since = runtime_state.regulator_high_armed_since.get(spec.name)
        last_action_time = runtime_state.regulator_last_action_time.get(spec.name, -math.inf)

        dss.Transformers.Name(spec.name)
        dss.Transformers.Wdg(2)
        current_tap = float(dss.Transformers.Tap())
        new_tap = current_tap

        if v_mon <= fidvr.regulator_low_voltage_pu:
            high_armed_since = None
            if low_armed_since is None:
                low_armed_since = current_time
            elif (
                current_time - low_armed_since >= fidvr.regulator_delay_s
                and current_time - last_action_time >= fidvr.regulator_tap_delay_s
            ):
                new_tap = min(spec.max_tap, current_tap + spec.tap_step)
        elif v_mon >= fidvr.regulator_high_voltage_pu:
            low_armed_since = None
            if high_armed_since is None:
                high_armed_since = current_time
            elif (
                current_time - high_armed_since >= fidvr.regulator_delay_s
                and current_time - last_action_time >= fidvr.regulator_tap_delay_s
            ):
                new_tap = max(spec.min_tap, current_tap - spec.tap_step)
        else:
            low_armed_since = None
            high_armed_since = None

        if abs(new_tap - current_tap) > 1e-9:
            dss.Text.Command(f"Transformer.{spec.name}.Taps=[1.0 {new_tap:.5f}]")
            last_action_time = current_time
        applied_taps.append(new_tap)

        runtime_state.regulator_low_armed_since[spec.name] = low_armed_since
        runtime_state.regulator_high_armed_since[spec.name] = high_armed_since
        runtime_state.regulator_last_action_time[spec.name] = last_action_time

    return _safe_mean(applied_taps)


def describe_fidvr_stage(
    current_time: float,
    disturbance: DisturbanceConfig,
    fidvr: FidvrConfig,
    runtime_state: FeederRuntimeState,
    regulator_specs: dict[str, RegulatorSpec],
    capacitor_specs: dict[str, CapacitorSpec],
    applied_tap: float,
) -> dict:
    timeline = get_fidvr_timeline(disturbance, fidvr)
    trigger_time = timeline["trigger_time"]
    clear_time = timeline["clear_time"]
    cap_fraction = get_capacitor_fraction(runtime_state, capacitor_specs)
    control_summary = collect_motor_control_summary(runtime_state)
    restore_frac = control_summary.get("motor_restore_frac", 1.0)
    thermal_restore_frac = control_summary.get("motor_thermal_restore_frac", 0.0)
    thermal_restoration_active = (
        control_summary.get("motor_thermal_restoring_groups", 0) > 0
        or control_summary.get("motor_thermal_restored_groups", 0) > 0
        or thermal_restore_frac > 0.02
    )
    baseline_tap = _average_baseline_tap(regulator_specs)

    if not fidvr.enabled:
        stage = "DISABLED"
    elif current_time + 1e-9 < trigger_time:
        stage = "BASELINE"
    elif current_time + 1e-9 < clear_time:
        stage = "FAULT_ACTIVE"
    elif control_summary.get("motor_stalled_groups", 0) > 0:
        stage = "STALLED_MOTORS"
    elif thermal_restoration_active:
        stage = "LOAD_RESTORATION"
    elif cap_fraction < 0.99:
        stage = "CAPS_OFF"
    elif (
        control_summary.get("motor_tripped_groups", 0) > 0
        and math.isfinite(applied_tap)
        and math.isfinite(baseline_tap)
        and applied_tap > baseline_tap + 0.01
    ):
        stage = "OVERSHOOT"
    else:
        stage = "RECOVERED"

    return {
        "stage": stage,
        "motor_p_scale": control_summary.get("motor_p_scale", 1.0),
        "motor_q_scale": control_summary.get("motor_q_scale", 1.0),
        "caps_status": shunt_status_from_fraction(cap_fraction),
        "cap_fraction": cap_fraction,
        "tap_offset": 0.0 if not math.isfinite(applied_tap) or not math.isfinite(baseline_tap) else applied_tap - baseline_tap,
        "restore_frac": restore_frac,
        "thermal_restore_frac": thermal_restore_frac,
        "source_target_pu": 1.0,
    }


def apply_fidvr_controls(
    load_specs: dict[str, LoadSpec],
    regulator_specs: dict[str, RegulatorSpec],
    capacitor_specs: dict[str, CapacitorSpec],
    fidvr: FidvrConfig,
    runtime_state: FeederRuntimeState,
    current_time: float,
    disturbance: DisturbanceConfig,
) -> float:
    """Apply all feeder side controls before the OpenDSS snapshot solve."""

    if not fidvr.enabled:
        _apply_baseline_loads(load_specs)
        for spec in capacitor_specs.values():
            runtime_state.capacitor_states[spec.name] = True
            runtime_state.capacitor_on_armed_since[spec.name] = None
            runtime_state.capacitor_off_armed_since[spec.name] = None
            runtime_state.capacitor_locked_out[spec.name] = False
            runtime_state.capacitor_last_action[spec.name] = ""
            runtime_state.capacitor_monitored_voltage[spec.name] = math.nan
            _set_enabled(f"Capacitor.{spec.name}", True)
        return _set_regulator_taps(regulator_specs, 0.0)

    if current_time + 1e-9 < disturbance.fault_time:
        for motor in runtime_state.motor_elements:
            runtime_state.motor_group_states[motor.element_name] = "running"
            runtime_state.motor_group_p_scales[motor.element_name] = 1.0
            runtime_state.motor_group_q_scales[motor.element_name] = 1.0
            runtime_state.motor_stall_armed_since[motor.element_name] = None
            runtime_state.motor_thermal_state[motor.element_name] = 0.0
            runtime_state.motor_trip_reason[motor.element_name] = ""
            runtime_state.motor_reconnect_armed_since[motor.element_name] = None
            runtime_state.motor_restore_frac[motor.element_name] = 1.0
            runtime_state.motor_contactor_fraction[motor.element_name] = 1.0
            runtime_state.motor_uv_trip_fraction[motor.element_name] = 0.0
            runtime_state.motor_uv1_armed_since[motor.element_name] = None
            runtime_state.motor_uv2_armed_since[motor.element_name] = None
            runtime_state.motor_thermal_state_a[motor.element_name] = 0.0
            runtime_state.motor_thermal_state_b[motor.element_name] = 0.0
            runtime_state.motor_wecc_a_stalled[motor.element_name] = False
            runtime_state.motor_wecc_b_stalled[motor.element_name] = False
            runtime_state.motor_wecc_b_restarted[motor.element_name] = False
            runtime_state.motor_thermal_restore_trip_time[motor.element_name] = None
            runtime_state.motor_thermal_restore_started_at[motor.element_name] = None
            runtime_state.motor_thermal_restore_frac[motor.element_name] = 0.0
            runtime_state.motor_thermal_restore_target[motor.element_name] = 0.0
            runtime_state.motor_thermal_restore_delay_s[motor.element_name] = (
                _thermal_restore_delay_s(motor.element_name, fidvr)
            )
            apply_motor_group_targets(
                runtime_state,
                motor,
                "running",
                motor.kw,
                motor.baseline_kvar,
            )
        initial_cap_fraction = (
            fidvr.initial_capacitor_fraction if fidvr.enable_cap_control else 1.0
        )
        _initialize_capacitor_states(capacitor_specs, runtime_state, initial_cap_fraction)
        for spec in regulator_specs.values():
            runtime_state.regulator_low_armed_since[spec.name] = None
            runtime_state.regulator_high_armed_since[spec.name] = None
            runtime_state.regulator_last_action_time[spec.name] = -math.inf
        runtime_state.last_control_time = current_time
        return _set_regulator_taps(regulator_specs, 0.0)

    dt = max(0.0, current_time - runtime_state.last_control_time)
    update_motor_group_states(runtime_state, fidvr, current_time, dt)
    if fidvr.enable_cap_control and current_time + 1e-9 >= disturbance.clear_time:
        update_capacitor_controls(capacitor_specs, fidvr, runtime_state, current_time)
    else:
        for spec in capacitor_specs.values():
            runtime_state.capacitor_states[spec.name] = (
                runtime_state.capacitor_states.get(spec.name, True)
                if fidvr.enable_cap_control and current_time + 1e-9 < disturbance.clear_time
                else True
            )
            runtime_state.capacitor_on_armed_since[spec.name] = None
            runtime_state.capacitor_off_armed_since[spec.name] = None
            runtime_state.capacitor_locked_out[spec.name] = False
            runtime_state.capacitor_last_action[spec.name] = ""
            runtime_state.capacitor_monitored_voltage[spec.name] = math.nan
            _set_enabled(
                f"Capacitor.{spec.name}",
                runtime_state.capacitor_states[spec.name],
            )

    if fidvr.enable_reg_control and current_time + 1e-9 >= disturbance.clear_time:
        applied_tap = update_regulator_controls(
            regulator_specs, fidvr, runtime_state, current_time
        )
    else:
        for spec in regulator_specs.values():
            runtime_state.regulator_low_armed_since[spec.name] = None
            runtime_state.regulator_high_armed_since[spec.name] = None
            runtime_state.regulator_last_action_time[spec.name] = -math.inf
        applied_tap = _set_regulator_taps(regulator_specs, 0.0)
    runtime_state.last_control_time = current_time
    return applied_tap


def finalize_stage_info(
    stage_info: dict,
    tx_v_pu: float,
    tx_angle_deg: float,
    effective_v_pu: float,
    applied_tap: float,
    regulator_specs: dict[str, RegulatorSpec],
    runtime_state: FeederRuntimeState,
) -> dict:
    finalized = dict(stage_info)
    finalized.update(
        {
            "tx_v_pu": tx_v_pu,
            "tx_angle_deg": tx_angle_deg,
            "effective_v_pu": effective_v_pu,
            "effective_voltage": _complex_from_polar(effective_v_pu, tx_angle_deg),
            "applied_tap": applied_tap,
        }
    )
    finalized.update(collect_regulator_tap_summary(regulator_specs))
    finalized.update(collect_motor_diagnostics(runtime_state))
    finalized.update(collect_motor_control_summary(runtime_state))
    return finalized


def format_regulator_taps_for_log(stage_info: dict) -> str:
    tokens = []
    for key in sorted(stage_info):
        if not key.startswith("reg_tap_") or key == "reg_tap_avg":
            continue
        tap_value = stage_info[key]
        if not math.isfinite(tap_value):
            continue
        suffix = key.removeprefix("reg_tap_")
        label = "".join(part.capitalize() for part in suffix.split("_"))
        tokens.append(f"Tap{label}={tap_value:.5f}")
    return " ".join(tokens)


def format_capacitor_states_for_log(
    capacitor_specs: dict[str, CapacitorSpec],
    runtime_state: FeederRuntimeState,
) -> str:
    tokens = []
    for spec in capacitor_specs.values():
        label = spec.name[:1].upper() + spec.name[1:]
        enabled = runtime_state.capacitor_states.get(spec.name, True)
        tokens.append(f"{label}={'on' if enabled else 'off'}")

        monitored_voltage = runtime_state.capacitor_monitored_voltage.get(
            spec.name, math.nan
        )
        if math.isfinite(monitored_voltage):
            tokens.append(f"{label}V={monitored_voltage:.6f}")

        if runtime_state.capacitor_locked_out.get(spec.name, False):
            tokens.append(f"{label}Lock=1")

        action = runtime_state.capacitor_last_action.get(spec.name, "")
        if action:
            tokens.append(f"{label}Action={action}")
    return " ".join(tokens)


# =============================================================================
# Distribution Solve Step
# =============================================================================

def run_snapshot_solution(
    raw_tx_voltage: complex,
    load_specs: dict[str, LoadSpec],
    regulator_specs: dict[str, RegulatorSpec],
    capacitor_specs: dict[str, CapacitorSpec],
    disturbance: DisturbanceConfig,
    fidvr: FidvrConfig,
    runtime_state: FeederRuntimeState,
    current_time: float,
):
    """Update feeder controls, solve OpenDSS, and summarize the solved state."""

    tx_v_pu = abs(raw_tx_voltage)
    tx_angle_deg = math.degrees(math.atan2(raw_tx_voltage.imag, raw_tx_voltage.real))
    applied_tap = apply_fidvr_controls(
        load_specs,
        regulator_specs,
        capacitor_specs,
        fidvr,
        runtime_state,
        current_time,
        disturbance,
    )
    effective_v_pu = tx_v_pu
    dss.Text.Command(
        f"Edit Vsource.Source pu={effective_v_pu:.6f} angle={tx_angle_deg:.6f}"
    )
    dss.Solution.Solve()
    if runtime_state.motor_elements and (
        not dss.Solution.Converged() or not _all_finite(dss.Circuit.TotalPower())
    ):
        dss.Solution.SolveDirect()
    stage_info = describe_fidvr_stage(
        current_time,
        disturbance,
        fidvr,
        runtime_state,
        regulator_specs,
        capacitor_specs,
        applied_tap,
    )
    finalized_stage_info = finalize_stage_info(
        stage_info,
        tx_v_pu,
        tx_angle_deg,
        effective_v_pu,
        applied_tap,
        regulator_specs,
        runtime_state,
    )
    runtime_state.last_stage_info = finalized_stage_info
    return finalized_stage_info


def solve_distribution_from_source(
    raw_tx_voltage: complex,
    load_specs: dict[str, LoadSpec],
    regulator_specs: dict[str, RegulatorSpec],
    capacitor_specs: dict[str, CapacitorSpec],
    disturbance: DisturbanceConfig,
    fidvr: FidvrConfig,
    runtime_state: FeederRuntimeState,
    dist_voltage_bus: str,
    base_mva: float,
    current_time: float,
):
    stage_info = run_snapshot_solution(
        raw_tx_voltage,
        load_specs,
        regulator_specs,
        capacitor_specs,
        disturbance,
        fidvr,
        runtime_state,
        current_time,
    )

    dist_bus_snapshot = get_bus_voltage_snapshot(dist_voltage_bus)
    alert_signal_info = build_alert_signal_info(
        fidvr,
        dist_voltage_bus,
        dist_bus_snapshot,
        stage_info["effective_v_pu"],
    )
    total_pq = dss.Circuit.TotalPower()
    pu_scale = 1000.0 * base_mva
    p_pu = -total_pq[0] / pu_scale
    q_pu = -total_pq[1] / pu_scale

    return complex(p_pu, q_pu), total_pq, dist_bus_snapshot, alert_signal_info, stage_info


# =============================================================================
# Runtime Setup and HELICS Loop
# =============================================================================

SCRIPT_DIR = Path(__file__).resolve().parent
DIST_MASTER_DSS = get_distribution_case_path(SCRIPT_DIR)
DIST_VOLTAGE_BUS = get_distribution_voltage_bus()
OUTPUT_DIR = get_output_dir(SCRIPT_DIR)
FEEDER_ALERT_CSV_PATH = OUTPUT_DIR / f"feeder_{feeder_index}_fidvr_alerts.csv"
FEEDER_ALL_BUS_VOLTAGE_CSV_PATH = (
    OUTPUT_DIR / f"feeder_{feeder_index}_all_bus_voltages.csv"
)
COSIM_BASE_MVA = get_cosim_base_mva()
DIST_LOAD_SCALE = get_distribution_load_scale()
BROKER_URL = get_broker_url()
VOLTAGE_TOPIC = get_voltage_topic()
POWER_TOPIC = get_feeder_power_topic(feeder_index)
HELICS_UNINTERRUPTIBLE = get_env_bool("HELICS_UNINTERRUPTIBLE", False)
LOG_ALL_BUS_VOLTAGES = get_all_bus_voltage_logging_enabled()
disturbance = get_disturbance_config()
fine_dt, coarse_dt, coarse_start = get_cosim_step_config(disturbance)
fidvr = get_fidvr_config()

if not DIST_MASTER_DSS.exists():
    raise FileNotFoundError(f"Distribution case not found: {DIST_MASTER_DSS}")

# 1. HELICS setup
fedinfo = h.helicsCreateFederateInfo()
h.helicsFederateInfoSetCoreTypeFromString(fedinfo, "zmq")
h.helicsFederateInfoSetCoreInitString(
    fedinfo, f"--broker={BROKER_URL}"
)
h.helicsFederateInfoSetTimeProperty(
    fedinfo, h.HELICS_PROPERTY_TIME_DELTA, min(fine_dt, coarse_dt)
)
h.helicsFederateInfoSetFlagOption(
    fedinfo, h.HELICS_FLAG_UNINTERRUPTIBLE, HELICS_UNINTERRUPTIBLE
)
dist_fed = h.helicsCreateValueFederate(f"Feeder{feeder_index}", fedinfo)

sub_v = h.helicsFederateRegisterSubscription(dist_fed, VOLTAGE_TOPIC, "")
pub_s = h.helicsFederateRegisterGlobalPublication(
    dist_fed, POWER_TOPIC, h.HELICS_DATA_TYPE_COMPLEX, ""
)
h.helicsPublicationSetOption(pub_s, h.HELICS_HANDLE_OPTION_ONLY_TRANSMIT_ON_CHANGE, 1)

print(f"Feeder {feeder_index}: HELICS interfaces created.")
print(
    "Feeder config: "
    f"case={DIST_MASTER_DSS} voltage_bus={DIST_VOLTAGE_BUS} "
    f"power_topic={POWER_TOPIC} voltage_topic={VOLTAGE_TOPIC} "
    f"broker={BROKER_URL} interface_base={COSIM_BASE_MVA:.3f} MVA "
    f"load_scale={DIST_LOAD_SCALE:.3f} "
    f"uninterruptible={HELICS_UNINTERRUPTIBLE} output_dir={OUTPUT_DIR}"
)
if abs(fine_dt - coarse_dt) < 1e-12:
    print(f"Feeder: co-simulation step schedule = constant {fine_dt:.3f}s")
else:
    print(
        "Feeder: co-simulation step schedule = "
        f"{fine_dt:.3f}s until t={coarse_start:.3f}s, then {coarse_dt:.3f}s"
    )
if fidvr.enabled:
    print(
        "Feeder FIDVR config: "
        "motor_model=wecc_motor_d "
        f"motors={','.join(fidvr.motor_loads)} "
        f"caps={','.join(fidvr.capacitor_names)} "
        f"regs={','.join(fidvr.regulator_names)} "
        f"wecc=[CompPF={fidvr.wecc_comp_pf:.3f},Vstall={fidvr.wecc_vstall:.3f},"
        f"Rstall={fidvr.wecc_rstall:.3f},Xstall={fidvr.wecc_xstall:.3f},"
        f"Tstall={fidvr.wecc_tstall:.3f},Frst={fidvr.wecc_frst:.3f},"
        f"Vrst={fidvr.wecc_vrst:.3f},Trst={fidvr.wecc_trst:.3f},"
        f"Tth={fidvr.wecc_tth:.1f},Th1t={fidvr.wecc_th1t:.2f},"
        f"Th2t={fidvr.wecc_th2t:.2f},Fuvr={fidvr.wecc_fuvr:.3f}] "
        f"thermal_restore={'on' if fidvr.enable_thermal_load_restoration else 'off'} "
        f"thermal_restore_window=[{fidvr.thermal_restore_min_delay_s:.1f},"
        f"{fidvr.thermal_restore_max_delay_s:.1f}]s "
        f"thermal_restore_ramp={fidvr.thermal_restore_ramp_s:.1f}s "
        f"thermal_restore_v={fidvr.thermal_restore_voltage_pu:.3f} pu "
        f"thermal_restore_fraction={fidvr.thermal_restore_fraction:.2f} "
        f"reg_control={'on' if fidvr.enable_reg_control else 'off'} "
        f"cap_control={'on' if fidvr.enable_cap_control else 'off'} "
        f"reg_band=[{fidvr.regulator_low_voltage_pu:.3f}, {fidvr.regulator_high_voltage_pu:.3f}] "
        f"cap_band=[{fidvr.capacitor_on_voltage_pu:.3f}, {fidvr.capacitor_off_voltage_pu:.3f}] "
        f"cap_init={fidvr.initial_capacitor_fraction:.2f} "
        f"cap_lockout={'on' if fidvr.capacitor_lockout_after_open else 'off'} "
        f"cap_kvar_scale={fidvr.capacitor_kvar_scale:.3f}"
    )
else:
    print("Feeder FIDVR config: disabled.")

# 2. OpenDSS setup
dss.Basic.ClearAll()
dss.Text.Command(f'Compile "{DIST_MASTER_DSS}"')
dss.Text.Command("set controlmode=off")
dss.Text.Command("set mode=snap")
dss.Text.Command("set maxcontroliter=100")

ALL_BUS_VOLTAGE_BUSES = get_all_bus_voltage_buses() if LOG_ALL_BUS_VOLTAGES else ()
if LOG_ALL_BUS_VOLTAGES:
    initialize_all_bus_voltage_csv(FEEDER_ALL_BUS_VOLTAGE_CSV_PATH)
    print(
        f"Feeder {feeder_index}: logging bus voltages for "
        f"{len(ALL_BUS_VOLTAGE_BUSES)} buses to {FEEDER_ALL_BUS_VOLTAGE_CSV_PATH}"
    )

load_specs = collect_load_specs(DIST_LOAD_SCALE)
regulator_specs = collect_regulator_specs(fidvr.regulator_names)
capacitor_specs = collect_capacitor_specs(
    fidvr.capacitor_names, fidvr.capacitor_kvar_scale
)
runtime_state = FeederRuntimeState()

_apply_baseline_loads(load_specs)
runtime_state.motor_elements = build_motor_elements(load_specs, fidvr)
for motor in runtime_state.motor_elements:
    runtime_state.motor_group_states[motor.element_name] = "running"
    runtime_state.motor_group_p_scales[motor.element_name] = 1.0
    runtime_state.motor_group_q_scales[motor.element_name] = 1.0
    runtime_state.motor_stall_armed_since[motor.element_name] = None
    runtime_state.motor_thermal_state[motor.element_name] = 0.0
    runtime_state.motor_trip_reason[motor.element_name] = ""
    runtime_state.motor_reconnect_armed_since[motor.element_name] = None
    runtime_state.motor_restore_frac[motor.element_name] = 1.0
    runtime_state.motor_contactor_fraction[motor.element_name] = 1.0
    runtime_state.motor_uv_trip_fraction[motor.element_name] = 0.0
    runtime_state.motor_uv1_armed_since[motor.element_name] = None
    runtime_state.motor_uv2_armed_since[motor.element_name] = None
    runtime_state.motor_thermal_state_a[motor.element_name] = 0.0
    runtime_state.motor_thermal_state_b[motor.element_name] = 0.0
    runtime_state.motor_wecc_a_stalled[motor.element_name] = False
    runtime_state.motor_wecc_b_stalled[motor.element_name] = False
    runtime_state.motor_wecc_b_restarted[motor.element_name] = False
    runtime_state.motor_thermal_restore_trip_time[motor.element_name] = None
    runtime_state.motor_thermal_restore_started_at[motor.element_name] = None
    runtime_state.motor_thermal_restore_frac[motor.element_name] = 0.0
    runtime_state.motor_thermal_restore_target[motor.element_name] = 0.0
    runtime_state.motor_thermal_restore_delay_s[motor.element_name] = (
        _thermal_restore_delay_s(motor.element_name, fidvr)
    )
initial_cap_fraction = (
    fidvr.initial_capacitor_fraction if fidvr.enable_cap_control else 1.0
)
_initialize_capacitor_states(capacitor_specs, runtime_state, initial_cap_fraction)
_set_regulator_taps(regulator_specs, 0.0)
dss.Text.Command("Edit Vsource.Source pu=1.030000 angle=0.000000")
dss.Solution.Solve()
if runtime_state.motor_elements and (
    not dss.Solution.Converged() or not _all_finite(dss.Circuit.TotalPower())
):
    dss.Solution.SolveDirect()

if runtime_state.motor_elements:
    total_motor_kw = sum(motor.kw for motor in runtime_state.motor_elements)
    total_motor_kva = sum(motor.kva for motor in runtime_state.motor_elements)
    print(
        "Feeder compressor motors: "
        f"count={len(runtime_state.motor_elements)} "
        "topology=single-phase-per-load backend=wecc-motor-d "
        f"total_kw={total_motor_kw:.3f} total_kva={total_motor_kva:.3f} "
        f"Frst={fidvr.wecc_frst:.3f} Fuvr={fidvr.wecc_fuvr:.3f}"
    )
    for motor in runtime_state.motor_elements:
        print(
            "  "
            f"{motor.element_name} source_load={motor.source_load_name} "
            f"group={motor.group_index + 1} "
            f"phase={motor.phase} phases={motor.phases} bus={motor.bus} "
            f"conn={motor.conn} kv={motor.kv:.3f} "
            f"kW={motor.kw:.3f} kVA={motor.kva:.3f} "
            f"baseline_kvar={motor.baseline_kvar:.3f} "
            f"stall_kW={motor.stall_kw:.3f} "
            f"stall_kvar={motor.stall_kvar:.3f} "
            f"Vstall={fidvr.wecc_vstall:.3f} "
            f"Tstall={fidvr.wecc_tstall:.3f}s "
            f"Vrst={fidvr.wecc_vrst:.3f} "
            f"Trst={fidvr.wecc_trst:.2f}s "
            f"thermal_restore_delay="
            f"{runtime_state.motor_thermal_restore_delay_s.get(motor.element_name, math.nan):.2f}s"
        )

PROFILE_24 = [1.0] * 24


def loadmult_from_time(t_sec: float) -> float:
    t_day = t_sec % 86400.0
    hour = t_day / 3600.0
    i0 = int(math.floor(hour)) % 24
    i1 = (i0 + 1) % 24
    frac = hour - math.floor(hour)
    return PROFILE_24[i0] * (1.0 - frac) + PROFILE_24[i1] * frac


last_time_applied = -1.0
last_loadmult = loadmult_from_time(0.0)
dss.Text.Command(f"set loadmult={last_loadmult:.4f}")

# ---- HELICS initialization handshake at t = 0 ----
max_init = 20
tol_init_v = 1e-6
tol_init_s = 1e-6

tx_voltage_last = 1.03 + 0.0j
tx_voltage_prev = None

(
    s_prev,
    total_pq,
    dist_bus_snapshot,
    alert_signal_info,
    stage_info,
) = solve_distribution_from_source(
    tx_voltage_last,
    load_specs,
    regulator_specs,
    capacitor_specs,
    disturbance,
    fidvr,
    runtime_state,
    DIST_VOLTAGE_BUS,
    COSIM_BASE_MVA,
    current_time=0.0,
)
if LOG_ALL_BUS_VOLTAGES:
    append_all_bus_voltage_rows(
        FEEDER_ALL_BUS_VOLTAGE_CSV_PATH,
        feeder_index,
        0,
        0.0,
        "INITIAL",
        ALL_BUS_VOLTAGE_BUSES,
    )
initial_alert_voltage = float(alert_signal_info["alert_v_pu"])

print(
    f"Feeder {feeder_index}: initial guess "
    f"TxV={stage_info['tx_v_pu']:.6f} pu "
    f"AppliedV={stage_info['effective_v_pu']:.6f} pu "
    f"P={s_prev.real:.6f} Q={s_prev.imag:.6f} "
    f"Stage={stage_info['stage']} "
    f"AlertSignal={alert_signal_info['alert_signal_mode']} "
    f"AlertBus={alert_signal_info['alert_bus']} "
    f"AlertV={initial_alert_voltage:.6f} pu"
)
log_prefault_voltage_health(
    feeder_index,
    fidvr,
    dist_bus_snapshot,
    alert_signal_info,
    float(stage_info["tx_v_pu"]),
)

h.helicsFederateEnterInitializingMode(dist_fed)
print(f"Feeder {feeder_index}: entered HELICS initialization mode.")
h.helicsPublicationPublishComplex(pub_s, s_prev)

last_fidvr_stage = None

for k in range(max_init):
    init_state = h.helicsFederateEnterExecutingModeIterative(
        dist_fed, h.HELICS_ITERATION_REQUEST_ITERATE_IF_NEEDED
    )

    if h.helicsInputIsUpdated(sub_v):
        tx_voltage_last = h.helicsInputGetComplex(sub_v)

    (
        s_new,
        total_pq,
        dist_bus_snapshot,
        alert_signal_info,
        stage_info,
    ) = solve_distribution_from_source(
        tx_voltage_last,
        load_specs,
        regulator_specs,
        capacitor_specs,
        disturbance,
        fidvr,
        runtime_state,
        DIST_VOLTAGE_BUS,
        COSIM_BASE_MVA,
        current_time=0.0,
    )

    d_v = float("inf") if tx_voltage_prev is None else abs(tx_voltage_last - tx_voltage_prev)
    d_s = abs(s_new - s_prev)

    print(
        f"[Feeder{feeder_index:02d} init {k + 1:02d}] "
        f"TxV={stage_info['tx_v_pu']:.6f} pu "
        f"AppliedV={stage_info['effective_v_pu']:.6f} pu "
        f"P={s_new.real:.6f} Q={s_new.imag:.6f} "
        f"dV={d_v:.3e} dS={d_s:.3e} "
        f"Stage={stage_info['stage']} "
        f"SlipAvg={stage_info['motor_slip_avg']:.6f} "
        f"SlipMax={stage_info['motor_slip_max']:.6f}"
    )

    if d_v > tol_init_v or d_s > tol_init_s:
        h.helicsPublicationPublishComplex(pub_s, s_new)

    tx_voltage_prev = tx_voltage_last
    s_prev = s_new

    if init_state == h.HELICS_ITERATION_RESULT_NEXT_STEP:
        break
else:
    raise RuntimeError(f"Feeder {feeder_index}: initialization handshake did not converge.")

print(f"Feeder {feeder_index}: initialization handshake converged.")
settled_alert_voltage = float(alert_signal_info["alert_v_pu"])
feeder_alert_label = str(alert_signal_info["alert_label"])
feeder_alert_detector = FidvrAlertDetector(reference_voltage_pu=settled_alert_voltage)
feeder_alert_detector.update(0.0, settled_alert_voltage)
print(
    f"Feeder {feeder_index}: alert detector reference set after initialization "
    f"AlertV={settled_alert_voltage:.6f} pu ({feeder_alert_label})."
)

# 3. Normal time loop starts here
target_time = get_target_time()
print(f"Feeder {feeder_index}: target simulation time = {target_time:.3f} s")
current_time = 0.0
iter_count = 0

while current_time < target_time:
    current_dt = fine_dt if current_time + 1e-9 < coarse_start else coarse_dt
    next_time = min(current_time + current_dt, target_time)
    step_state_snapshot = copy.deepcopy(runtime_state)
    step_loadmult = last_loadmult
    step_loadmult_time = last_time_applied
    max_outer = 20

    for _ in range(max_outer):
        runtime_state = copy.deepcopy(step_state_snapshot)
        candidate_loadmult = step_loadmult
        candidate_loadmult_time = step_loadmult_time

        granted_time, iteration_state = h.helicsFederateRequestTimeIterative(
            dist_fed, next_time, h.HELICS_ITERATION_REQUEST_ITERATE_IF_NEEDED
        )
        iter_count += 1

        # FINE_DT controls how often this block runs during the fault window.
        # That makes it the Motor D timer resolution for accepted time steps.
        updated = h.helicsInputIsUpdated(sub_v)
        if updated:
            tx_voltage_last = h.helicsInputGetComplex(sub_v)

        if granted_time > candidate_loadmult_time + 1e-9:
            candidate_loadmult = loadmult_from_time(granted_time)
            dss.Text.Command(f"set loadmult={candidate_loadmult:.4f}")
            candidate_loadmult_time = granted_time

        (
            s_new,
            total_pq,
            dist_bus_snapshot,
            alert_signal_info,
            stage_info,
        ) = solve_distribution_from_source(
            tx_voltage_last,
            load_specs,
            regulator_specs,
            capacitor_specs,
            disturbance,
            fidvr,
            runtime_state,
            DIST_VOLTAGE_BUS,
            COSIM_BASE_MVA,
            current_time=granted_time,
        )
        h.helicsPublicationPublishComplex(pub_s, s_new)

        if iteration_state == h.HELICS_ITERATION_RESULT_ITERATING:
            continue

        current_time = granted_time
        last_loadmult = candidate_loadmult
        last_time_applied = candidate_loadmult_time

        regulator_tap_log = format_regulator_taps_for_log(stage_info)
        capacitor_state_log = format_capacitor_states_for_log(
            capacitor_specs, runtime_state
        )
        control_state_log = " ".join(
            token for token in (regulator_tap_log, capacitor_state_log) if token
        )
        control_state_suffix = f" {control_state_log}" if control_state_log else ""

        if fidvr.enabled and stage_info["stage"] != last_fidvr_stage:
            print(
                f"[Feeder{feeder_index:02d} FIDVR] "
                f"t={current_time:.3f}s stage={stage_info['stage']} "
                f"TxV={stage_info['tx_v_pu']:.6f} pu "
                f"AppliedV={stage_info['effective_v_pu']:.6f} pu "
                f"Caps={stage_info['caps_status']} "
                f"CapFrac={stage_info['cap_fraction']:.3f} "
                f"Tap={stage_info['applied_tap']:.5f} "
                f"Restore={stage_info['restore_frac']:.3f} "
                f"ThermalRestore={stage_info.get('thermal_restore_frac', 0.0):.3f} "
                f"SlipAvg={stage_info['motor_slip_avg']:.6f} "
                f"SlipMax={stage_info['motor_slip_max']:.6f} "
                f"MotorPF={stage_info['motor_pf_avg']:.6f} "
                f"Running={stage_info.get('motor_running_groups', 0)} "
                f"Stalled={stage_info.get('motor_stalled_groups', 0)} "
                f"Tripped={stage_info.get('motor_tripped_groups', 0)} "
                f"Restoring={stage_info.get('motor_restoring_groups', 0)} "
                f"ContactorTrips={stage_info.get('motor_contactor_open_groups', 0)} "
                f"ThermalTrips={stage_info.get('motor_thermal_trip_groups', 0)} "
                f"LockedOut={stage_info.get('motor_locked_out_groups', 0)}"
                f"{control_state_suffix}"
            )
        last_fidvr_stage = stage_info["stage"]
        feeder_alert_label = str(alert_signal_info["alert_label"])

        state_str = ITER_STATE_NAME.get(iteration_state, str(iteration_state))
        dist_alert_voltage_pu = float(alert_signal_info["alert_v_pu"])
        for alert in feeder_alert_detector.update(current_time, dist_alert_voltage_pu):
            print(
                f"[Feeder{feeder_index:02d} ALERT] "
                f"t={alert.trigger_time_s:.3f}s {alert.alert_id} {alert.alert_name} "
                f"V={alert.trigger_voltage_pu:.6f} pu | {alert.details}"
            )
        if LOG_ALL_BUS_VOLTAGES:
            append_all_bus_voltage_rows(
                FEEDER_ALL_BUS_VOLTAGE_CSV_PATH,
                feeder_index,
                iter_count,
                current_time,
                state_str,
                ALL_BUS_VOLTAGE_BUSES,
            )
        print(
            f"[Feeder{feeder_index:02d}] "
            f"iter={iter_count:06d} "
            f"t_granted={current_time:.3f}s (t_req={next_time:.3f}s, dt={current_dt:.3f}s) "
            f"state={state_str} | "
            f"Vupdate={updated} V={stage_info['effective_v_pu']:.6f} pu "
            f"ang={stage_info['tx_angle_deg']:.6f} deg | "
            f"DistBus={dist_bus_snapshot['bus']} "
            f"Vavg={dist_bus_snapshot['avg_mag']:.6f} pu "
            f"Va={_phase_value(dist_bus_snapshot['phase_mags'], 1):.6f} pu "
            f"Vb={_phase_value(dist_bus_snapshot['phase_mags'], 2):.6f} pu "
            f"Vc={_phase_value(dist_bus_snapshot['phase_mags'], 3):.6f} pu "
            f"Vpos={dist_bus_snapshot['positive_seq_mag']:.6f} pu "
            f"AlertSignal={alert_signal_info['alert_signal_mode']} "
            f"AlertBus={alert_signal_info['alert_bus']} "
            f"AlertV={float(alert_signal_info['alert_v_pu']):.6f} pu "
            f"AlertVpos={float(alert_signal_info['alert_vpos_pu']):.6f} pu "
            f"AlertVavg={float(alert_signal_info['alert_vavg_pu']):.6f} pu | "
            f"TotalPower={total_pq[0]:.2f} kW, {total_pq[1]:.2f} kvar | "
            f"Pub={s_new.real:.6f}+j{s_new.imag:.6f} pu "
            f"LoadMult={last_loadmult:.4f} | "
            f"FIDVR={stage_info['stage']} "
            f"TxV={stage_info['tx_v_pu']:.6f} "
            f"MotorP={stage_info['motor_p_scale']:.3f} "
            f"MotorQ={stage_info['motor_q_scale']:.3f} "
            f"Caps={stage_info['caps_status']} "
            f"CapFrac={stage_info['cap_fraction']:.3f} "
            f"Tap={stage_info['applied_tap']:.5f} "
            f"Restore={stage_info['restore_frac']:.3f} "
            f"ThermalRestore={stage_info.get('thermal_restore_frac', 0.0):.3f} "
            f"SlipAvg={stage_info['motor_slip_avg']:.6f} "
            f"SlipMax={stage_info['motor_slip_max']:.6f} "
            f"MotorPF={stage_info['motor_pf_avg']:.6f} "
            f"Running={stage_info.get('motor_running_groups', 0)} "
            f"Stalled={stage_info.get('motor_stalled_groups', 0)} "
            f"Tripped={stage_info.get('motor_tripped_groups', 0)} "
            f"Restoring={stage_info.get('motor_restoring_groups', 0)} "
            f"ContactorTrips={stage_info.get('motor_contactor_open_groups', 0)} "
            f"ThermalTrips={stage_info.get('motor_thermal_trip_groups', 0)} "
            f"LockedOut={stage_info.get('motor_locked_out_groups', 0)}"
            f"{control_state_suffix}"
        )
        break
    else:
        raise RuntimeError(
            f"Feeder {feeder_index}: hit max_outer={max_outer} at "
            f"t={current_time:.3f}s while requesting {next_time:.3f}s."
        )

feeder_alerts = feeder_alert_detector.to_dataframe()
feeder_alerts.to_csv(FEEDER_ALERT_CSV_PATH, index=False)
print(f"Feeder {feeder_index}: saved alert CSV to {FEEDER_ALERT_CSV_PATH}")
for line in alert_summary_lines(
    feeder_alerts, f"Feeder {feeder_index} {feeder_alert_label}"
):
    print(f"[Feeder{feeder_index:02d} ALERT SUMMARY] {line}")

h.helicsFederateDisconnect(dist_fed)
h.helicsFederateFree(dist_fed)
print(f"Feeder {feeder_index}: Finished.")
