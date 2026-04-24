#!/usr/bin/env python3
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import helics as h
import opendssdirect as dss

from fidvr_alerts import FidvrAlertDetector, alert_summary_lines


ITER_STATE_NAME = {
    h.HELICS_ITERATION_RESULT_NEXT_STEP: "NEXT_STEP",
    h.HELICS_ITERATION_RESULT_ITERATING: "ITERATING",
    h.HELICS_ITERATION_RESULT_ERROR: "ERROR",
    h.HELICS_ITERATION_RESULT_HALTED: "HALTED",
}


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


@dataclass(frozen=True)
class DisturbanceConfig:
    enabled: bool
    fault_time: float
    fault_duration: float
    clear_time: float


@dataclass(frozen=True)
class MotorElementSpec:
    element_name: str
    companion_load_name: str
    source_load_name: str
    dynamic_element: bool
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
    trip_offset_s: float
    restore_offset_s: float


@dataclass
class FeederRuntimeState:
    dynamics_enabled: bool = False
    dynamic_time: float = 0.0
    motor_elements: tuple[MotorElementSpec, ...] = ()
    motor_group_states: dict[str, str] = field(default_factory=dict)
    motor_group_p_scales: dict[str, float] = field(default_factory=dict)
    motor_group_q_scales: dict[str, float] = field(default_factory=dict)
    motor_stall_armed_since: dict[str, float | None] = field(default_factory=dict)
    motor_thermal_state: dict[str, float] = field(default_factory=dict)
    motor_trip_until: dict[str, float] = field(default_factory=dict)
    motor_reconnect_armed_since: dict[str, float | None] = field(default_factory=dict)
    motor_restore_started_at: dict[str, float | None] = field(default_factory=dict)
    motor_restore_frac: dict[str, float] = field(default_factory=dict)
    regulator_low_armed_since: dict[str, float | None] = field(default_factory=dict)
    regulator_high_armed_since: dict[str, float | None] = field(default_factory=dict)
    regulator_last_action_time: dict[str, float] = field(default_factory=dict)
    capacitor_on_armed_since: dict[str, float | None] = field(default_factory=dict)
    capacitor_off_armed_since: dict[str, float | None] = field(default_factory=dict)
    capacitor_states: dict[str, bool] = field(default_factory=dict)
    last_control_time: float = 0.0
    last_stage_info: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FidvrConfig:
    enabled: bool
    motor_model: str
    motor_loads: tuple[str, ...]
    capacitor_names: tuple[str, ...]
    regulator_names: tuple[str, ...]
    motor_share: float
    stall_kvar_per_kw: float
    tripped_motor_p_scale: float
    tripped_motor_q_scale: float
    motor_kva_factor: float
    motor_group_trip_offsets: tuple[float, ...]
    motor_group_restore_offsets: tuple[float, ...]
    static_kvar_per_motor_kw: float
    motor_stall_voltage_pu: float
    motor_stall_clear_voltage_pu: float
    motor_stall_delay_s: float
    motor_stall_kw_scale: float
    motor_stall_kvar_scale: float
    motor_thermal_trip_level: float
    motor_thermal_reset_level: float
    motor_thermal_trip_time_s: float
    motor_thermal_trip_spread_s: float
    motor_cool_time_s: float
    motor_reconnect_delay_s: float
    motor_reconnect_ramp_s: float
    motor_reconnect_voltage_pu: float
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
    dynamic_step: float
    enable_reg_control: bool
    enable_cap_control: bool
    alert_signal: str
    alert_bus: str


feeder_index = int(sys.argv[1]) if len(sys.argv) > 1 else 1


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


def get_env_float_list(name: str, default: str) -> tuple[float, ...]:
    raw = os.environ.get(name, default)
    values = []
    for entry in raw.split(","):
        item = entry.strip()
        if not item:
            continue
        try:
            values.append(float(item))
        except ValueError as exc:
            raise ValueError(
                f"Invalid {name}='{raw}'. Expected a comma-separated list of floats."
            ) from exc
    if not values:
        raise ValueError(
            f"Invalid {name}='{raw}'. Expected a comma-separated list of floats."
        )
    return tuple(values)


def _expand_sequence(values: tuple, count: int, name: str) -> tuple:
    if len(values) == count:
        return values
    if len(values) == 1:
        return values * count
    raise ValueError(
        f"Invalid {name}: expected 1 or {count} entries, got {len(values)}."
    )


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
    if not case_path.is_absolute():
        case_path = script_dir / case_path
    return case_path.resolve()


def get_distribution_voltage_bus() -> str:
    return os.environ.get("DIST_VOLTAGE_BUS", "650").strip()


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


def get_fidvr_config(fine_dt: float) -> FidvrConfig:
    """Configure FIDVR motor model optimized for fault-based validation.
    
    Key tuning for fault scenario:
    - Stall voltage: 0.70-0.75 (captures deep sag from fault)
    - Stall kvar scale: 6.0-8.0 (reactive power surge during stall)
    - Motor reconnect delay: 8-12s (multi-second delayed recovery)
    - Thermal trip time: 15-20s (holds stalled state)
    """
    requested_motor_model = get_env_choice(
        "FIDVR_MOTOR_MODEL", "surrogate", {"actual", "surrogate", "indmach"}
    )
    if requested_motor_model == "actual":
        print(
            "Feeder FIDVR config: FIDVR_MOTOR_MODEL=actual is deprecated in this "
            "repository; using the surrogate staged-load backend."
        )
        motor_model = "surrogate"
    else:
        motor_model = requested_motor_model

    motor_share_default = 0.45 if motor_model == "surrogate" else 0.35
    motor_group_trip_offsets = get_env_float_list(
        "FIDVR_MOTOR_GROUP_TRIP_OFFSETS", "0,0,0"
    )
    motor_group_restore_offsets = get_env_float_list(
        "FIDVR_MOTOR_GROUP_RESTORE_OFFSETS", "0,0,0"
    )
    return FidvrConfig(
        enabled=get_env_bool("FIDVR_ENABLE", False),
        motor_model=motor_model,
        motor_loads=get_env_name_list(
            "FIDVR_MOTOR_LOADS",
            "634a,634b,634c,645,675a,675b,675c,611,652,670a,670b,670c",
        ),
        capacitor_names=get_env_name_list("FIDVR_CAPACITORS", "cap1,cap2"),
        regulator_names=get_env_name_list("FIDVR_REGULATORS", "reg1,reg2,reg3"),
        motor_share=get_fraction_env_float("FIDVR_MOTOR_SHARE", motor_share_default),
        stall_kvar_per_kw=get_positive_env_float("FIDVR_STALL_KVAR_PER_KW", 2.0),
        tripped_motor_p_scale=get_nonnegative_env_float(
            "FIDVR_TRIPPED_MOTOR_P_SCALE", 0.02
        ),
        tripped_motor_q_scale=get_nonnegative_env_float(
            "FIDVR_TRIPPED_MOTOR_Q_SCALE", 0.02
        ),
        motor_kva_factor=get_positive_env_float("FIDVR_MOTOR_KVA_FACTOR", 1.0 / 0.92),
        motor_group_trip_offsets=motor_group_trip_offsets,
        motor_group_restore_offsets=motor_group_restore_offsets,
        static_kvar_per_motor_kw=get_nonnegative_env_float(
            "FIDVR_STATIC_KVAR_PER_MOTOR_KW", 0.25
        ),
        motor_stall_voltage_pu=get_positive_env_float("FIDVR_MOTOR_STALL_VOLTAGE_PU", 0.62),
        motor_stall_clear_voltage_pu=get_positive_env_float(
            "FIDVR_MOTOR_STALL_CLEAR_VOLTAGE_PU", 0.88
        ),
        motor_stall_delay_s=get_positive_env_float("FIDVR_MOTOR_STALL_DELAY_S", 0.05),
        motor_stall_kw_scale=get_nonnegative_env_float(
            "FIDVR_MOTOR_STALL_KW_SCALE", 0.50
        ),
        motor_stall_kvar_scale=get_positive_env_float(
            "FIDVR_MOTOR_STALL_KVAR_SCALE", 4.0
        ),
        motor_thermal_trip_level=get_positive_env_float(
            "FIDVR_MOTOR_THERMAL_TRIP_LEVEL", 1.0
        ),
        motor_thermal_reset_level=get_fraction_env_float(
            "FIDVR_MOTOR_THERMAL_RESET_LEVEL", 0.35
        ),
        motor_thermal_trip_time_s=get_positive_env_float(
            "FIDVR_MOTOR_THERMAL_TRIP_TIME_S", 10.0
        ),
        motor_thermal_trip_spread_s=get_nonnegative_env_float(
            "FIDVR_MOTOR_THERMAL_TRIP_SPREAD_S", 0.0
        ),
        motor_cool_time_s=get_positive_env_float("FIDVR_MOTOR_COOL_TIME_S", 30.0),
        motor_reconnect_delay_s=get_positive_env_float(
            "FIDVR_MOTOR_RECONNECT_DELAY_S", 8.0
        ),
        motor_reconnect_ramp_s=get_positive_env_float(
            "FIDVR_MOTOR_RECONNECT_RAMP_S", 4.0
        ),
        motor_reconnect_voltage_pu=get_positive_env_float(
            "FIDVR_MOTOR_RECONNECT_VOLTAGE_PU", 0.95
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
        dynamic_step=get_positive_env_float("FIDVR_DYNAMIC_STEP", min(fine_dt / 5.0, 0.001)),
        enable_reg_control=get_env_bool("FIDVR_ENABLE_REG_CONTROL", False),
        enable_cap_control=get_env_bool("FIDVR_ENABLE_CAP_CONTROL", False),
        alert_signal=get_env_choice(
            "FIDVR_ALERT_SIGNAL", "dist_bus", {"dist_bus", "source", "bus"}
        ),
        alert_bus=os.environ.get("FIDVR_ALERT_BUS", "").strip(),
    )


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


def collect_capacitor_specs(capacitor_names: tuple[str, ...]) -> dict[str, CapacitorSpec]:
    specs = {}
    available = {name.lower() for name in dss.Capacitors.AllNames()}
    for name in capacitor_names:
        if name not in available:
            raise RuntimeError(
                f"FIDVR capacitor '{name}' was not found in the OpenDSS case."
            )
        dss.Circuit.SetActiveElement(f"Capacitor.{name}")
        bus_name = str(dss.CktElement.BusNames()[0])
        specs[name] = CapacitorSpec(
            name=name,
            bus=bus_name,
            phases=int(dss.CktElement.NumPhases()),
            kv=float(dss.Capacitors.kV()),
        )
    return specs


def _apply_baseline_loads(load_specs: dict[str, LoadSpec]) -> None:
    for spec in load_specs.values():
        dss.Text.Command(
            f"Edit Load.{spec.name} kW={spec.kw:.6f} kvar={spec.kvar:.6f}"
        )


def _set_capacitors(capacitor_names: tuple[str, ...], enabled: bool) -> None:
    _set_capacitor_fraction(capacitor_names, 1.0 if enabled else 0.0)


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

def build_surrogate_motors(
    load_specs: dict[str, LoadSpec],
    fidvr: FidvrConfig,
) -> tuple[MotorElementSpec, ...]:
    if not fidvr.enabled or fidvr.motor_model != "surrogate":
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

        static_kw = max(1e-3, spec.kw - motor_kw)
        static_kvar = max(1e-3, spec.kvar - fidvr.static_kvar_per_motor_kw * motor_kw)
        dss.Text.Command(
            f"Edit Load.{spec.name} kW={static_kw:.6f} kvar={static_kvar:.6f}"
        )

        kva = max(1e-3, motor_kw * fidvr.motor_kva_factor)
        motor_name = f"comp_{spec.name}"
        baseline_kvar = max(1e-3, motor_kw * fidvr.static_kvar_per_motor_kw)
        stall_kw = max(1e-3, motor_kw * fidvr.motor_stall_kw_scale)
        stall_kvar = max(
            1e-3,
            motor_kw * fidvr.stall_kvar_per_kw,
            baseline_kvar * fidvr.motor_stall_kvar_scale,
        )
        trip_offset = fidvr.motor_group_trip_offsets[
            motor_index % len(fidvr.motor_group_trip_offsets)
        ]
        restore_offset = fidvr.motor_group_restore_offsets[
            motor_index % len(fidvr.motor_group_restore_offsets)
        ]
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
                    "model=3",
                    "status=variable",
                    "vminpu=0.30",
                    "vmaxpu=1.50",
                ]
            )
        )
        motor_specs.append(
            MotorElementSpec(
                element_name=f"Load.{motor_name}",
                companion_load_name=f"Load.{motor_name}",
                source_load_name=spec.name,
                dynamic_element=False,
                group_index=len(motor_specs),
                phase=phase,
                phases=spec.phases,
                kw=motor_kw,
                kva=kva,
                baseline_kvar=baseline_kvar,
                stall_kw=stall_kw,
                stall_kvar=stall_kvar,
                bus=spec.bus,
                kv=spec.kv,
                conn=spec.conn,
                trip_offset_s=trip_offset,
                restore_offset_s=restore_offset,
            )
        )

    if skipped_multiphase:
        print(
            "Feeder compressor motors: skipped non-single-phase loads for the 1-phase motor "
            f"conversion: {', '.join(skipped_multiphase)}"
        )

    if not motor_specs:
        return ()

    return tuple(motor_specs)


def build_motor_elements(
    load_specs: dict[str, LoadSpec],
    fidvr: FidvrConfig,
) -> tuple[MotorElementSpec, ...]:
    if not fidvr.enabled:
        return ()
    if fidvr.motor_model == "surrogate":
        return build_surrogate_motors(load_specs, fidvr)
    if fidvr.motor_model == "indmach":
        raise RuntimeError(
            "FIDVR_MOTOR_MODEL=indmach is not implemented in this repository yet. "
            "Use FIDVR_MOTOR_MODEL=surrogate for the staged-load FIDVR workflow."
        )
    raise RuntimeError(f"Unsupported FIDVR motor model: {fidvr.motor_model}")


def collect_motor_diagnostics(runtime_state: FeederRuntimeState) -> dict:
    slips = []
    power_factors = []
    for motor in runtime_state.motor_elements:
        if not motor.dynamic_element:
            continue
        if runtime_state.motor_group_states.get(motor.element_name, "dynamic") != "dynamic":
            continue
        dss.Circuit.SetActiveElement(motor.element_name)
        variable_names = list(dss.CktElement.AllVariableNames())
        variable_values = list(dss.CktElement.AllVariableValues())
        variable_map = dict(zip(variable_names, variable_values))
        slip = float(variable_map.get("Slip", math.nan))
        power_factor = float(variable_map.get("Power Factor", math.nan))
        if math.isfinite(slip):
            slips.append(slip)
        if math.isfinite(power_factor):
            power_factors.append(power_factor)

    return {
        "motor_slip_avg": _safe_mean(slips),
        "motor_slip_max": max(slips) if slips else math.nan,
        "motor_pf_avg": _safe_mean(power_factors),
    }


def collect_motor_control_summary(runtime_state: FeederRuntimeState) -> dict:
    if not runtime_state.motor_elements:
        return {}

    p_scales = []
    q_scales = []
    restore_fracs = []
    mode_counts = {"running": 0, "stalled": 0, "tripped": 0, "restoring": 0}

    for motor in runtime_state.motor_elements:
        mode = runtime_state.motor_group_states.get(motor.element_name, "running")
        if mode not in mode_counts:
            mode = "restoring"
        mode_counts[mode] += 1
        p_scales.append(runtime_state.motor_group_p_scales.get(motor.element_name, 1.0))
        q_scales.append(runtime_state.motor_group_q_scales.get(motor.element_name, 1.0))
        restore_fracs.append(runtime_state.motor_restore_frac.get(motor.element_name, 1.0))

    return {
        "motor_p_scale": _safe_mean(p_scales),
        "motor_q_scale": _safe_mean(q_scales),
        "motor_restore_frac": _safe_mean(restore_fracs),
        "motor_running_groups": mode_counts["running"],
        "motor_stalled_groups": mode_counts["stalled"],
        "motor_tripped_groups": mode_counts["tripped"],
        "motor_restoring_groups": mode_counts["restoring"],
    }


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


def enter_dynamic_mode_if_needed(
    runtime_state: FeederRuntimeState,
    fidvr: FidvrConfig,
    start_time: float,
) -> None:
    if runtime_state.dynamics_enabled or not runtime_state.motor_elements:
        return

    dynamic_motors = [motor for motor in runtime_state.motor_elements if motor.dynamic_element]
    if not dynamic_motors:
        return

    dss.Solution.SolveDirect()
    for motor in dynamic_motors:
        dss.Text.Command(f"Edit {motor.element_name} SlipOption=variableslip")
    dss.Text.Command("set mode=dynamics")
    dss.Solution.Number(1)
    dss.Solution.StepSize(fidvr.dynamic_step)
    runtime_state.dynamics_enabled = True
    runtime_state.dynamic_time = start_time


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


def _smoothstep(progress: float) -> float:
    clipped = max(0.0, min(1.0, progress))
    return clipped * clipped * (3.0 - 2.0 * clipped)


def _ramped_value(
    current_time: float,
    start_time: float,
    duration: float,
    start_value: float,
    end_value: float,
) -> float:
    if current_time <= start_time:
        return start_value
    if duration <= 0.0 or current_time >= start_time + duration:
        return end_value
    progress = _smoothstep((current_time - start_time) / duration)
    return start_value + progress * (end_value - start_value)


def _stage_progress(current_time: float, start_time: float, end_time: float) -> float:
    duration = max(end_time - start_time, 1e-9)
    return max(0.0, min(1.0, (current_time - start_time) / duration))


def _average_baseline_tap(regulator_specs: dict[str, RegulatorSpec]) -> float:
    if not regulator_specs:
        return math.nan
    return sum(spec.baseline_tap for spec in regulator_specs.values()) / len(
        regulator_specs
    )


def _thermal_trip_time_for_motor(motor: MotorElementSpec, fidvr: FidvrConfig) -> float:
    pattern_len = max(len(fidvr.motor_group_trip_offsets), 1)
    position = (motor.group_index % pattern_len) / max(pattern_len - 1, 1)
    trip_time = (
        fidvr.motor_thermal_trip_time_s
        + motor.trip_offset_s
        + fidvr.motor_thermal_trip_spread_s * position
    )
    return max(2.0, trip_time)


def _thermal_heating_rate(v_motor: float, fidvr: FidvrConfig, trip_time: float) -> float:
    severity = max(0.0, fidvr.motor_stall_voltage_pu - v_motor)
    multiplier = 1.0 + 2.0 * severity / max(fidvr.motor_stall_voltage_pu, 1e-6)
    return multiplier / trip_time


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

    if not motor.dynamic_element:
        if group_mode == "running":
            target_kw = motor.kw
            target_kvar = motor.baseline_kvar
        dss.Text.Command(
            f"Edit {motor.element_name} kW={target_kw:.6f} kvar={target_kvar:.6f}"
        )
        runtime_state.motor_group_states[motor.element_name] = group_mode
        runtime_state.motor_group_p_scales[motor.element_name] = target_kw / max(
            motor.kw, 1e-6
        )
        runtime_state.motor_group_q_scales[motor.element_name] = target_kvar / max(
            motor.baseline_kvar, 1e-6
        )
        return

    current_mode = runtime_state.motor_group_states.get(motor.element_name)
    if current_mode != group_mode:
        use_dynamic = group_mode == "running"
        _set_enabled(motor.element_name, use_dynamic)
        _set_enabled(motor.companion_load_name, not use_dynamic)
        runtime_state.motor_group_states[motor.element_name] = group_mode

    if group_mode == "running":
        dss.Text.Command(f"Edit {motor.element_name} kW={motor.kw:.6f}")
        runtime_state.motor_group_p_scales[motor.element_name] = 1.0
        runtime_state.motor_group_q_scales[motor.element_name] = 1.0
        return

    dss.Text.Command(
        f"Edit {motor.companion_load_name} kW={target_kw:.6f} kvar={target_kvar:.6f}"
    )
    runtime_state.motor_group_p_scales[motor.element_name] = target_kw / max(motor.kw, 1e-6)
    runtime_state.motor_group_q_scales[motor.element_name] = target_kvar / max(
        motor.baseline_kvar, 1e-6
    )


def update_motor_group_states(
    runtime_state: FeederRuntimeState,
    fidvr: FidvrConfig,
    current_time: float,
    dt: float,
) -> None:
    for motor in runtime_state.motor_elements:
        v_motor = get_monitored_bus_voltage_pu(motor.bus)
        element = motor.element_name
        state = runtime_state.motor_group_states.get(element, "running")
        thermal = runtime_state.motor_thermal_state.get(element, 0.0)
        stall_armed_since = runtime_state.motor_stall_armed_since.get(element)
        reconnect_armed_since = runtime_state.motor_reconnect_armed_since.get(element)
        restore_started_at = runtime_state.motor_restore_started_at.get(element)
        trip_until = runtime_state.motor_trip_until.get(element, 0.0)

        thermal = max(0.0, thermal - dt / max(fidvr.motor_cool_time_s, 1e-6))
        restore_frac = 1.0 if state == "running" else 0.0

        if state == "running":
            if v_motor <= fidvr.motor_stall_voltage_pu:
                if stall_armed_since is None:
                    stall_armed_since = current_time
                elif current_time - stall_armed_since >= fidvr.motor_stall_delay_s:
                    state = "stalled"
                    stall_armed_since = None
            elif v_motor >= fidvr.motor_stall_clear_voltage_pu:
                stall_armed_since = None
        elif state == "stalled":
            thermal += dt * _thermal_heating_rate(
                v_motor, fidvr, _thermal_trip_time_for_motor(motor, fidvr)
            )
            if thermal >= fidvr.motor_thermal_trip_level:
                state = "tripped"
                trip_until = current_time + max(0.0, motor.restore_offset_s)
                reconnect_armed_since = None
                restore_started_at = None
            restore_frac = 0.0
        elif state == "tripped":
            thermal = max(0.0, thermal - dt / max(fidvr.motor_cool_time_s, 1e-6))
            if (
                thermal <= fidvr.motor_thermal_reset_level
                and current_time >= trip_until
                and v_motor >= fidvr.motor_reconnect_voltage_pu
            ):
                if reconnect_armed_since is None:
                    reconnect_armed_since = current_time
                elif (
                    current_time - reconnect_armed_since
                    >= fidvr.motor_reconnect_delay_s
                ):
                    state = "restoring"
                    restore_started_at = current_time
                    reconnect_armed_since = None
            else:
                reconnect_armed_since = None
            restore_frac = 0.0
        elif state == "restoring":
            thermal = max(0.0, thermal - dt / max(fidvr.motor_cool_time_s, 1e-6))
            if v_motor < fidvr.motor_stall_voltage_pu:
                state = "stalled"
                stall_armed_since = None
                restore_started_at = None
                restore_frac = 0.0
            else:
                restore_started_at = (
                    current_time if restore_started_at is None else restore_started_at
                )
                restore_frac = min(
                    1.0,
                    (current_time - restore_started_at)
                    / max(fidvr.motor_reconnect_ramp_s, 1e-6),
                )
                if restore_frac >= 1.0 - 1e-9:
                    state = "running"
                    restore_started_at = None
                    restore_frac = 1.0

        if state == "running":
            target_kw = motor.kw
            target_kvar = motor.baseline_kvar
            restore_frac = 1.0
        elif state == "stalled":
            target_kw = motor.stall_kw
            target_kvar = motor.stall_kvar
            restore_frac = 0.0
        elif state == "tripped":
            target_kw = max(1e-3, motor.kw * fidvr.tripped_motor_p_scale)
            target_kvar = max(
                1e-3, motor.baseline_kvar * fidvr.tripped_motor_q_scale
            )
            restore_frac = 0.0
        else:
            start_kw = max(1e-3, motor.kw * fidvr.tripped_motor_p_scale)
            start_kvar = max(1e-3, motor.baseline_kvar * fidvr.tripped_motor_q_scale)
            target_kw = start_kw + restore_frac * (motor.kw - start_kw)
            target_kvar = start_kvar + restore_frac * (motor.baseline_kvar - start_kvar)

        runtime_state.motor_group_states[element] = state
        runtime_state.motor_stall_armed_since[element] = stall_armed_since
        runtime_state.motor_thermal_state[element] = thermal
        runtime_state.motor_trip_until[element] = trip_until
        runtime_state.motor_reconnect_armed_since[element] = reconnect_armed_since
        runtime_state.motor_restore_started_at[element] = restore_started_at
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
        is_enabled = runtime_state.capacitor_states.get(spec.name, True)
        on_armed_since = runtime_state.capacitor_on_armed_since.get(spec.name)
        off_armed_since = runtime_state.capacitor_off_armed_since.get(spec.name)

        if is_enabled:
            on_armed_since = None
            if v_cap >= fidvr.capacitor_off_voltage_pu:
                if off_armed_since is None:
                    off_armed_since = current_time
                elif current_time - off_armed_since >= fidvr.capacitor_off_delay_s:
                    is_enabled = False
                    off_armed_since = None
            elif v_cap <= fidvr.capacitor_off_voltage_pu - 0.01:
                off_armed_since = None
        else:
            off_armed_since = None
            if v_cap <= fidvr.capacitor_on_voltage_pu:
                if on_armed_since is None:
                    on_armed_since = current_time
                elif current_time - on_armed_since >= fidvr.capacitor_on_delay_s:
                    is_enabled = True
                    on_armed_since = None
            elif v_cap >= fidvr.capacitor_on_voltage_pu + 0.01:
                on_armed_since = None

        runtime_state.capacitor_states[spec.name] = is_enabled
        runtime_state.capacitor_on_armed_since[spec.name] = on_armed_since
        runtime_state.capacitor_off_armed_since[spec.name] = off_armed_since
        _set_enabled(f"Capacitor.{spec.name}", is_enabled)

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
    baseline_tap = _average_baseline_tap(regulator_specs)

    if not fidvr.enabled:
        stage = "DISABLED"
    elif current_time + 1e-9 < trigger_time:
        stage = "BASELINE"
    elif current_time + 1e-9 < clear_time:
        stage = "FAULT_ACTIVE"
    elif control_summary.get("motor_stalled_groups", 0) > 0:
        stage = "STALLED_MOTORS"
    elif control_summary.get("motor_restoring_groups", 0) > 0 or restore_frac < 0.98:
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
        "caps_on": cap_fraction >= 0.5,
        "cap_fraction": cap_fraction,
        "tap_offset": 0.0 if not math.isfinite(applied_tap) or not math.isfinite(baseline_tap) else applied_tap - baseline_tap,
        "restore_frac": restore_frac,
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
    if not fidvr.enabled:
        _apply_baseline_loads(load_specs)
        for spec in capacitor_specs.values():
            runtime_state.capacitor_states[spec.name] = True
            _set_enabled(f"Capacitor.{spec.name}", True)
        return _set_regulator_taps(regulator_specs, 0.0)

    if current_time + 1e-9 < disturbance.fault_time:
        for motor in runtime_state.motor_elements:
            runtime_state.motor_group_states[motor.element_name] = "running"
            runtime_state.motor_group_p_scales[motor.element_name] = 1.0
            runtime_state.motor_group_q_scales[motor.element_name] = 1.0
            runtime_state.motor_stall_armed_since[motor.element_name] = None
            runtime_state.motor_thermal_state[motor.element_name] = 0.0
            runtime_state.motor_trip_until[motor.element_name] = 0.0
            runtime_state.motor_reconnect_armed_since[motor.element_name] = None
            runtime_state.motor_restore_started_at[motor.element_name] = None
            runtime_state.motor_restore_frac[motor.element_name] = 1.0
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
            "dynamics_enabled": runtime_state.dynamics_enabled,
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


def run_dynamic_solution(
    raw_tx_voltage: complex,
    load_specs: dict[str, LoadSpec],
    regulator_specs: dict[str, RegulatorSpec],
    capacitor_specs: dict[str, CapacitorSpec],
    disturbance: DisturbanceConfig,
    fidvr: FidvrConfig,
    runtime_state: FeederRuntimeState,
    current_time: float,
):
    tx_v_pu = abs(raw_tx_voltage)
    tx_angle_deg = math.degrees(math.atan2(raw_tx_voltage.imag, raw_tx_voltage.real))

    if current_time + 1e-12 < runtime_state.dynamic_time:
        raise RuntimeError(
            f"Requested dynamic solve at t={current_time:.6f}s but the dynamic "
            f"state is already at t={runtime_state.dynamic_time:.6f}s."
        )

    last_stage_info = dict(runtime_state.last_stage_info)

    if current_time <= runtime_state.dynamic_time + 1e-12:
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
        dss.Solution.SolveSnap()
        stage_info = describe_fidvr_stage(
            current_time,
            disturbance,
            fidvr,
            runtime_state,
            regulator_specs,
            capacitor_specs,
            applied_tap,
        )
        last_stage_info = finalize_stage_info(
            stage_info,
            tx_v_pu,
            tx_angle_deg,
            effective_v_pu,
            applied_tap,
            regulator_specs,
            runtime_state,
        )
    else:
        while runtime_state.dynamic_time + 1e-12 < current_time:
            next_time = min(current_time, runtime_state.dynamic_time + fidvr.dynamic_step)
            step_size = max(1e-6, next_time - runtime_state.dynamic_time)
            applied_tap = apply_fidvr_controls(
                load_specs,
                regulator_specs,
                capacitor_specs,
                fidvr,
                runtime_state,
                next_time,
                disturbance,
            )
            effective_v_pu = tx_v_pu
            dss.Solution.StepSize(step_size)
            dss.Text.Command(
                f"Edit Vsource.Source pu={effective_v_pu:.6f} angle={tx_angle_deg:.6f}"
            )
            dss.Solution.Solve()
            runtime_state.dynamic_time = next_time
            stage_info = describe_fidvr_stage(
                next_time,
                disturbance,
                fidvr,
                runtime_state,
                regulator_specs,
                capacitor_specs,
                applied_tap,
            )
            last_stage_info = finalize_stage_info(
                stage_info,
                tx_v_pu,
                tx_angle_deg,
                effective_v_pu,
                applied_tap,
                regulator_specs,
                runtime_state,
            )

    runtime_state.last_stage_info = last_stage_info
    return last_stage_info


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
    if runtime_state.dynamics_enabled:
        stage_info = run_dynamic_solution(
            raw_tx_voltage,
            load_specs,
            regulator_specs,
            capacitor_specs,
            disturbance,
            fidvr,
            runtime_state,
            current_time,
        )
    else:
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


SCRIPT_DIR = Path(__file__).resolve().parent
DIST_MASTER_DSS = get_distribution_case_path(SCRIPT_DIR)
DIST_VOLTAGE_BUS = get_distribution_voltage_bus()
FEEDER_ALERT_CSV_PATH = SCRIPT_DIR / f"feeder_{feeder_index}_fidvr_alerts.csv"
COSIM_BASE_MVA = get_cosim_base_mva()
DIST_LOAD_SCALE = get_distribution_load_scale()
BROKER_URL = get_broker_url()
VOLTAGE_TOPIC = get_voltage_topic()
POWER_TOPIC = get_feeder_power_topic(feeder_index)
HELICS_UNINTERRUPTIBLE = get_env_bool("HELICS_UNINTERRUPTIBLE", False)
disturbance = get_disturbance_config()
fine_dt, coarse_dt, coarse_start = get_cosim_step_config(disturbance)
fidvr = get_fidvr_config(fine_dt)

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
    f"uninterruptible={HELICS_UNINTERRUPTIBLE}"
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
        f"motor_model={fidvr.motor_model} "
        f"motors={','.join(fidvr.motor_loads)} "
        f"trip_offsets={','.join(f'{offset:.1f}' for offset in fidvr.motor_group_trip_offsets)} "
        f"restore_offsets={','.join(f'{offset:.1f}' for offset in fidvr.motor_group_restore_offsets)} "
        f"caps={','.join(fidvr.capacitor_names)} "
        f"regs={','.join(fidvr.regulator_names)} "
        f"stall_v={fidvr.motor_stall_voltage_pu:.3f} pu "
        f"trip_time={fidvr.motor_thermal_trip_time_s:.1f}s "
        f"reconnect_v={fidvr.motor_reconnect_voltage_pu:.3f} pu "
        f"dynamic_step={fidvr.dynamic_step:.4f}s "
        f"reg_control={'on' if fidvr.enable_reg_control else 'off'} "
        f"cap_control={'on' if fidvr.enable_cap_control else 'off'} "
        f"reg_band=[{fidvr.regulator_low_voltage_pu:.3f}, {fidvr.regulator_high_voltage_pu:.3f}] "
        f"cap_band=[{fidvr.capacitor_on_voltage_pu:.3f}, {fidvr.capacitor_off_voltage_pu:.3f}] "
        f"cap_init={fidvr.initial_capacitor_fraction:.2f}"
    )
else:
    print("Feeder FIDVR config: disabled.")

# 2. OpenDSS setup
dss.Basic.ClearAll()
dss.Text.Command(f'Compile "{DIST_MASTER_DSS}"')
dss.Text.Command("set controlmode=off")
dss.Text.Command("set mode=snap")
dss.Text.Command("set maxcontroliter=100")

load_specs = collect_load_specs(DIST_LOAD_SCALE)
regulator_specs = collect_regulator_specs(fidvr.regulator_names)
capacitor_specs = collect_capacitor_specs(fidvr.capacitor_names)
runtime_state = FeederRuntimeState()

_apply_baseline_loads(load_specs)
runtime_state.motor_elements = build_motor_elements(load_specs, fidvr)
for motor in runtime_state.motor_elements:
    runtime_state.motor_group_states[motor.element_name] = "running"
    runtime_state.motor_group_p_scales[motor.element_name] = 1.0
    runtime_state.motor_group_q_scales[motor.element_name] = 1.0
    runtime_state.motor_stall_armed_since[motor.element_name] = None
    runtime_state.motor_thermal_state[motor.element_name] = 0.0
    runtime_state.motor_trip_until[motor.element_name] = 0.0
    runtime_state.motor_reconnect_armed_since[motor.element_name] = None
    runtime_state.motor_restore_started_at[motor.element_name] = None
    runtime_state.motor_restore_frac[motor.element_name] = 1.0
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
        "topology=single-phase-per-load backend=surrogate-staged-load "
        f"total_kw={total_motor_kw:.3f} total_kva={total_motor_kva:.3f}"
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
            f"trip_offset={motor.trip_offset_s:.2f}s "
            f"restore_offset={motor.restore_offset_s:.2f}s "
            f"companion={motor.companion_load_name}"
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
initial_alert_voltage = float(alert_signal_info["alert_v_pu"])
feeder_alert_detector = FidvrAlertDetector(reference_voltage_pu=initial_alert_voltage)
feeder_alert_detector.update(0.0, initial_alert_voltage)
feeder_alert_label = str(alert_signal_info["alert_label"])

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

# 3. Normal time loop starts here
target_time = get_target_time()
print(f"Feeder {feeder_index}: target simulation time = {target_time:.3f} s")
current_time = 0.0
iter_count = 0

while current_time < target_time:
    current_dt = fine_dt if current_time + 1e-9 < coarse_start else coarse_dt
    next_time = min(current_time + current_dt, target_time)
    granted_time, iteration_state = h.helicsFederateRequestTimeIterative(
        dist_fed, next_time, h.HELICS_ITERATION_REQUEST_ITERATE_IF_NEEDED
    )

    current_time = granted_time
    iter_count += 1

    updated = h.helicsInputIsUpdated(sub_v)
    if updated:
        tx_voltage_last = h.helicsInputGetComplex(sub_v)

    if current_time > last_time_applied + 1e-9:
        last_loadmult = loadmult_from_time(current_time)
        dss.Text.Command(f"set loadmult={last_loadmult:.4f}")
        last_time_applied = current_time

    if (
        runtime_state.motor_elements
        and not runtime_state.dynamics_enabled
        and current_time + 1e-9 >= disturbance.fault_time
    ):
        enter_dynamic_mode_if_needed(runtime_state, fidvr, current_time)

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
        current_time=current_time,
    )
    h.helicsPublicationPublishComplex(pub_s, s_new)
    regulator_tap_log = format_regulator_taps_for_log(stage_info)
    regulator_tap_suffix = f" {regulator_tap_log}" if regulator_tap_log else ""

    if fidvr.enabled and stage_info["stage"] != last_fidvr_stage:
        print(
            f"[Feeder{feeder_index:02d} FIDVR] "
            f"t={current_time:.3f}s stage={stage_info['stage']} "
            f"TxV={stage_info['tx_v_pu']:.6f} pu "
            f"AppliedV={stage_info['effective_v_pu']:.6f} pu "
            f"Caps={'on' if stage_info['caps_on'] else 'off'} "
            f"CapFrac={stage_info['cap_fraction']:.3f} "
            f"Tap={stage_info['applied_tap']:.5f} "
            f"Restore={stage_info['restore_frac']:.3f} "
            f"SlipAvg={stage_info['motor_slip_avg']:.6f} "
            f"SlipMax={stage_info['motor_slip_max']:.6f} "
            f"MotorPF={stage_info['motor_pf_avg']:.6f} "
            f"Running={stage_info.get('motor_running_groups', 0)} "
            f"Stalled={stage_info.get('motor_stalled_groups', 0)} "
            f"Tripped={stage_info.get('motor_tripped_groups', 0)} "
            f"Restoring={stage_info.get('motor_restoring_groups', 0)} "
            f"Dyn={'on' if runtime_state.dynamics_enabled else 'off'}"
            f"{regulator_tap_suffix}"
        )
    last_fidvr_stage = stage_info["stage"]
    feeder_alert_label = str(alert_signal_info["alert_label"])

    state_str = ITER_STATE_NAME.get(iteration_state, str(iteration_state))
    if iteration_state != h.HELICS_ITERATION_RESULT_ITERATING:
        dist_alert_voltage_pu = float(alert_signal_info["alert_v_pu"])
        for alert in feeder_alert_detector.update(current_time, dist_alert_voltage_pu):
            print(
                f"[Feeder{feeder_index:02d} ALERT] "
                f"t={alert.trigger_time_s:.3f}s {alert.alert_id} {alert.alert_name} "
                f"V={alert.trigger_voltage_pu:.6f} pu | {alert.details}"
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
        f"Caps={'on' if stage_info['caps_on'] else 'off'} "
        f"CapFrac={stage_info['cap_fraction']:.3f} "
        f"Tap={stage_info['applied_tap']:.5f} "
        f"Restore={stage_info['restore_frac']:.3f} "
        f"SlipAvg={stage_info['motor_slip_avg']:.6f} "
        f"SlipMax={stage_info['motor_slip_max']:.6f} "
        f"MotorPF={stage_info['motor_pf_avg']:.6f} "
        f"Running={stage_info.get('motor_running_groups', 0)} "
        f"Stalled={stage_info.get('motor_stalled_groups', 0)} "
        f"Tripped={stage_info.get('motor_tripped_groups', 0)} "
        f"Restoring={stage_info.get('motor_restoring_groups', 0)} "
        f"Dyn={'on' if runtime_state.dynamics_enabled else 'off'}"
        f"{regulator_tap_suffix}"
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
