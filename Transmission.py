# Transmission.py
import helics as h
import andes
import math
import os
from pathlib import Path
import pandas as pd
import numpy as np

from fidvr_alerts import FidvrAlertDetector, alert_summary_lines


TRANSMISSION_CSV_COLUMNS = [
    "iter",
    "outer_iter",
    "t_granted",
    "cosim_dt",
    "tx_tds_step",
    "state",
    "updated",
    "P_total",
    "Q_total",
    "Vmag",
    "Vang_rad",
    "fault_idx",
    "fault_bus",
    "fault_active",
    "fault_rf",
    "fault_xf",
    "fault_bus_vmag",
    "fault_bus_vang_rad",
    "event_line_idx",
    "event_bus1",
    "event_bus2",
    "event_line_status",
    "event_bus1_vmag",
    "event_bus1_vang_rad",
    "event_bus2_vmag",
    "event_bus2_vang_rad",
    "event_bus_angle_diff_deg",
    "postfault_line_idx",
    "postfault_bus1",
    "postfault_bus2",
    "postfault_line_status",
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

ITER_STATE_NAME = {
    h.HELICS_ITERATION_RESULT_NEXT_STEP: "NEXT_STEP",
    h.HELICS_ITERATION_RESULT_ITERATING: "ITERATING",
    h.HELICS_ITERATION_RESULT_ERROR: "ERROR",
    h.HELICS_ITERATION_RESULT_HALTED: "HALTED",
}


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
            f"Invalid {name}='{value}'. Expected a positive float in seconds."
        ) from exc
    if parsed <= 0.0:
        raise ValueError(
            f"Invalid {name}='{value}'. Expected a positive float in seconds."
        )
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
        raise ValueError(
            f"Invalid {name}='{value}'. Expected a non-negative float."
        )
    return parsed


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


def get_positive_env_int(name: str, default: int) -> int:
    value = os.environ.get(name, str(default))
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(
            f"Invalid {name}='{value}'. Expected a positive integer."
        ) from exc
    if parsed <= 0:
        raise ValueError(
            f"Invalid {name}='{value}'. Expected a positive integer."
        )
    return parsed


def get_broker_url() -> str:
    return os.environ.get("HELICS_BROKER_URL", "tcp://127.0.0.1:23406")


def get_voltage_topic() -> str:
    return os.environ.get("TX_VOLTAGE_TOPIC", "TxInterfaceVoltage")


def get_feeder_power_topic(feeder_index: int) -> str:
    prefix = os.environ.get("DIST_POWER_TOPIC_PREFIX", "Feeder")
    return f"{prefix}{feeder_index}_Power"


def get_transmission_case_path(script_dir: Path) -> Path:
    case_value = os.environ.get("TX_CASE_XLSX", "ieee14_fault.xlsx")
    case_path = Path(case_value)
    if not case_path.is_absolute():
        case_path = script_dir / case_path
    return case_path.resolve()


def get_interface_bus() -> int:
    return get_positive_env_int("TX_INTERFACE_BUS", 2)


def get_feeder_count() -> int:
    return get_positive_env_int("FEEDER_COUNT", 1)


def get_cosim_base_mva() -> float:
    return get_positive_env_float("COSIM_BASE_MVA", 100.0)


def parse_postfault_lines() -> list[str]:
    """Parse optional post-fault line trips.

    Prefer TX_POSTFAULT_* but keep TX_DISTURBANCE_* as compatibility aliases.
    """
    for env_name in ("TX_POSTFAULT_LINES", "TX_DISTURBANCE_LINES"):
        raw = os.environ.get(env_name)
        if raw is None:
            continue
        line_indices = [item.strip() for item in raw.split(",") if item.strip()]
        if line_indices:
            return line_indices

    for env_name in ("TX_POSTFAULT_LINE", "TX_DISTURBANCE_LINE"):
        fallback = os.environ.get(env_name, "").strip()
        if fallback:
            return [fallback]
    return []


def get_disturbance_config():
    """Configure a true ANDES fault with optional post-fault topology stress."""
    enabled = get_env_bool("TX_ENABLE_DISTURBANCE", True)
    fault_bus = get_positive_env_int("TX_FAULT_BUS", get_interface_bus())
    fault_time = get_positive_env_float(
        "TX_FAULT_TIME",
        get_positive_env_float("TX_DISTURBANCE_TIME", 1.0),
    )
    fault_duration = get_positive_env_float(
        "TX_FAULT_DURATION",
        get_positive_env_float("TX_DISTURBANCE_DURATION", 0.08),
    )
    fault_rf = get_nonnegative_env_float("TX_FAULT_RF", 0.0)
    # The IEEE14 dynamic case needs a numerically softer fault reactance than
    # the near-zero starting point often used in textbook examples.
    fault_xf = get_nonnegative_env_float("TX_FAULT_XF", 0.3)
    fault_clear_time = fault_time + fault_duration

    line_indices = parse_postfault_lines()
    postfault_line_enabled = len(line_indices) > 0
    postfault_trip_delay = get_nonnegative_env_float("TX_POSTFAULT_TRIP_DELAY", 0.01)
    postfault_trip_time = (
        fault_clear_time + postfault_trip_delay if postfault_line_enabled else None
    )

    return {
        "enabled": enabled,
        "fault_idx": "Fault_Interface",
        "fault_bus": fault_bus,
        "fault_time": fault_time,
        "fault_duration": fault_duration,
        "fault_clear_time": fault_clear_time,
        "fault_rf": fault_rf,
        "fault_xf": fault_xf,
        "postfault_line_enabled": postfault_line_enabled,
        "line_indices": line_indices,
        "line_idx": line_indices[0] if line_indices else None,
        "postfault_trip_time": postfault_trip_time,
    }


def get_cosim_step_config(disturbance):
    """Get co-simulation time stepping with finer resolution around the fault event."""
    fine_dt = get_positive_env_float("SIM_FINE_DT", 0.005)
    coarse_dt = get_positive_env_float("SIM_COARSE_DT", 0.02)
    if disturbance["enabled"]:
        # Keep fine stepping well past fault recovery onset
        coarse_start_default = disturbance["fault_clear_time"] + 0.5
    else:
        coarse_start_default = 0.5
    coarse_start = get_positive_env_float("SIM_COARSE_START", coarse_start_default)
    if coarse_dt < fine_dt:
        raise ValueError(
            f"Invalid co-simulation step schedule: SIM_COARSE_DT={coarse_dt} "
            f"must be >= SIM_FINE_DT={fine_dt}."
        )
    return fine_dt, coarse_dt, coarse_start


def get_tds_internal_step(fine_dt: float) -> float:
    """Get transmission TDS internal step."""
    default_step = min(fine_dt / 5.0, 0.001)
    return get_positive_env_float("TX_TDS_STEP", default_step)


def scale_power_to_system_base(
    total_p: float,
    total_q: float,
    interface_base_mva: float,
    system_base_mva: float,
):
    scale = interface_base_mva / system_base_mva
    return total_p * scale, total_q * scale


def disable_built_in_disturbances(ss):
    for model_name in ("Fault", "Toggle", "Toggler"):
        if hasattr(ss, model_name):
            model = getattr(ss, model_name)
            if getattr(model, "n", 0) > 0 and hasattr(model, "u"):
                for i in range(model.n):
                    model.u.v[i] = 0



def split_line_for_parallel_trip(ss, base_idx: str, parallel_idx: str):
    # Optional helper kept for future studies where a synthetic parallel
    # branch is useful; the current contingency uses an existing double-circuit
    # line and does not call this path.
    base_uid = ss.Line.idx2uid(base_idx)

    def _value(field):
        return getattr(ss.Line, field).v[base_uid]

    base_name = str(_value("name"))
    line_params = {
        "idx": parallel_idx,
        "name": f"{base_name} parallel",
        "u": int(_value("u")),
        "bus1": int(_value("bus1")),
        "bus2": int(_value("bus2")),
        "Sn": float(_value("Sn")),
        "fn": float(_value("fn")),
        "Vn1": float(_value("Vn1")),
        "Vn2": float(_value("Vn2")),
        "r": float(_value("r")) * 2.0,
        "x": float(_value("x")) * 2.0,
        "b": float(_value("b")) / 2.0,
        "g": float(_value("g")) / 2.0,
        "b1": float(_value("b1")) / 2.0,
        "g1": float(_value("g1")) / 2.0,
        "b2": float(_value("b2")) / 2.0,
        "g2": float(_value("g2")) / 2.0,
        "trans": int(_value("trans")),
        "tap": float(_value("tap")),
        "phi": float(_value("phi")),
        "rate_a": float(_value("rate_a")),
        "rate_b": float(_value("rate_b")),
        "rate_c": float(_value("rate_c")),
    }

    ss.Line.r.v[base_uid] = line_params["r"]
    ss.Line.x.v[base_uid] = line_params["x"]
    ss.Line.b.v[base_uid] = line_params["b"]
    ss.Line.g.v[base_uid] = line_params["g"]
    ss.Line.b1.v[base_uid] = line_params["b1"]
    ss.Line.g1.v[base_uid] = line_params["g1"]
    ss.Line.b2.v[base_uid] = line_params["b2"]
    ss.Line.g2.v[base_uid] = line_params["g2"]

    ss.add("Line", line_params)

    return {
        "base_idx": base_idx,
        "base_name": base_name,
        "parallel_idx": parallel_idx,
        "bus1": line_params["bus1"],
        "bus2": line_params["bus2"],
        "r_each": line_params["r"],
        "x_each": line_params["x"],
        "b_each": line_params["b"],
    }


def get_total_feeder_power(subs, last_s):
    total_P = 0.0
    total_Q = 0.0
    updated_cnt = 0
    for i, sub in enumerate(subs):
        if h.helicsInputIsUpdated(sub):
            last_s[i] = h.helicsInputGetComplex(sub)
            updated_cnt += 1
        s_val = last_s[i]
        total_P += s_val.real
        total_Q += s_val.imag
    return total_P, total_Q, updated_cnt

def get_tx_bus_voltage(ss, bus_uid):
    vmag = ss.dae.y[ss.Bus.v.a[bus_uid]]
    vang = ss.dae.y[ss.Bus.a.a[bus_uid]]
    v = vmag * complex(math.cos(vang), math.sin(vang))
    return v, vmag, vang


def wrap_angle_deg(angle_deg):
    return (angle_deg + 180.0) % 360.0 - 180.0


def get_fault_diagnostics(ss, fault_uid, fault_idx, fault_bus, fault_bus_uid, disturbance):
    if None in {fault_uid, fault_idx, fault_bus, fault_bus_uid}:
        return {
            "fault_idx": str(fault_idx) if fault_idx is not None else None,
            "fault_bus": int(fault_bus) if fault_bus is not None else math.nan,
            "fault_active": math.nan,
            "fault_rf": float(disturbance["fault_rf"]),
            "fault_xf": float(disturbance["fault_xf"]),
            "fault_bus_vmag": math.nan,
            "fault_bus_vang_rad": math.nan,
        }

    _, fault_bus_vmag, fault_bus_vang = get_tx_bus_voltage(ss, fault_bus_uid)
    return {
        "fault_idx": str(fault_idx),
        "fault_bus": int(fault_bus),
        "fault_active": float(ss.Fault.uf.v[fault_uid]),
        "fault_rf": float(disturbance["fault_rf"]),
        "fault_xf": float(disturbance["fault_xf"]),
        "fault_bus_vmag": float(fault_bus_vmag),
        "fault_bus_vang_rad": float(fault_bus_vang),
    }


def get_event_line_diagnostics(
    ss,
    line_uid,
    line_idx,
    bus1,
    bus2,
    bus1_uid,
    bus2_uid,
):
    if None in {line_uid, bus1, bus2, bus1_uid, bus2_uid}:
        return {
            "event_line_idx": str(line_idx) if line_idx is not None else None,
            "event_bus1": math.nan,
            "event_bus2": math.nan,
            "event_line_status": math.nan,
            "event_bus1_vmag": math.nan,
            "event_bus1_vang_rad": math.nan,
            "event_bus2_vmag": math.nan,
            "event_bus2_vang_rad": math.nan,
            "event_bus_angle_diff_deg": math.nan,
            "postfault_line_idx": str(line_idx) if line_idx is not None else None,
            "postfault_bus1": math.nan,
            "postfault_bus2": math.nan,
            "postfault_line_status": math.nan,
        }

    _, bus1_vmag, bus1_vang = get_tx_bus_voltage(ss, bus1_uid)
    _, bus2_vmag, bus2_vang = get_tx_bus_voltage(ss, bus2_uid)

    return {
        "event_line_idx": str(line_idx),
        "event_bus1": int(bus1),
        "event_bus2": int(bus2),
        "event_line_status": float(ss.Line.u.v[line_uid]),
        "event_bus1_vmag": float(bus1_vmag),
        "event_bus1_vang_rad": float(bus1_vang),
        "event_bus2_vmag": float(bus2_vmag),
        "event_bus2_vang_rad": float(bus2_vang),
        "event_bus_angle_diff_deg": float(
            wrap_angle_deg(math.degrees(bus1_vang - bus2_vang))
        ),
        "postfault_line_idx": str(line_idx),
        "postfault_bus1": int(bus1),
        "postfault_bus2": int(bus2),
        "postfault_line_status": float(ss.Line.u.v[line_uid]),
    }


def get_genrou_diagnostics(ss):
    diag = {
        "delta_min_deg": math.nan,
        "delta_max_deg": math.nan,
        "delta_spread_deg": math.nan,
        "delta_min_idx": None,
        "delta_min_bus": math.nan,
        "delta_max_idx": None,
        "delta_max_bus": math.nan,
        "omega_min_pu": math.nan,
        "omega_max_pu": math.nan,
        "omega_max_dev": math.nan,
        "omega_min_idx": None,
        "omega_min_bus": math.nan,
        "omega_max_idx": None,
        "omega_max_bus": math.nan,
        "vf_min_pu": math.nan,
        "vf_max_pu": math.nan,
        "vf_min_idx": None,
        "vf_min_bus": math.nan,
        "vf_max_idx": None,
        "vf_max_bus": math.nan,
    }

    if not hasattr(ss, "GENROU") or getattr(ss.GENROU, "n", 0) <= 0:
        return diag

    def _values(var):
        if not hasattr(var, "v"):
            return np.asarray([], dtype=float)
        return np.asarray(var.v, dtype=float)

    delta = _values(ss.GENROU.delta)
    omega = _values(ss.GENROU.omega)
    vf = _values(ss.GENROU.vf)
    gen_ids = np.asarray(ss.GENROU.idx.v, dtype=object)
    gen_buses = np.asarray(ss.GENROU.bus.v, dtype=int)

    if delta.size:
        delta_deg = np.rad2deg(delta)
        delta_min_pos = int(np.argmin(delta_deg))
        delta_max_pos = int(np.argmax(delta_deg))
        diag["delta_min_deg"] = float(delta_deg[delta_min_pos])
        diag["delta_max_deg"] = float(delta_deg[delta_max_pos])
        diag["delta_spread_deg"] = float(delta_deg[delta_max_pos] - delta_deg[delta_min_pos])
        diag["delta_min_idx"] = str(gen_ids[delta_min_pos])
        diag["delta_min_bus"] = int(gen_buses[delta_min_pos])
        diag["delta_max_idx"] = str(gen_ids[delta_max_pos])
        diag["delta_max_bus"] = int(gen_buses[delta_max_pos])

    if omega.size:
        omega_min_pos = int(np.argmin(omega))
        omega_max_pos = int(np.argmax(omega))
        diag["omega_min_pu"] = float(omega[omega_min_pos])
        diag["omega_max_pu"] = float(omega[omega_max_pos])
        diag["omega_max_dev"] = float(np.max(np.abs(omega - 1.0)))
        diag["omega_min_idx"] = str(gen_ids[omega_min_pos])
        diag["omega_min_bus"] = int(gen_buses[omega_min_pos])
        diag["omega_max_idx"] = str(gen_ids[omega_max_pos])
        diag["omega_max_bus"] = int(gen_buses[omega_max_pos])

    if vf.size:
        vf_min_pos = int(np.argmin(vf))
        vf_max_pos = int(np.argmax(vf))
        diag["vf_min_pu"] = float(vf[vf_min_pos])
        diag["vf_max_pu"] = float(vf[vf_max_pos])
        diag["vf_min_idx"] = str(gen_ids[vf_min_pos])
        diag["vf_min_bus"] = int(gen_buses[vf_min_pos])
        diag["vf_max_idx"] = str(gen_ids[vf_max_pos])
        diag["vf_max_bus"] = int(gen_buses[vf_max_pos])

    return diag


def make_timeseries_row(
    iteration,
    outer_iter,
    t_granted,
    cosim_dt,
    tx_tds_step,
    state,
    updated,
    total_p,
    total_q,
    vmag,
    vang_rad,
    diagnostics=None,
):
    row = {
        "iter": int(iteration),
        "outer_iter": int(outer_iter),
        "t_granted": float(t_granted),
        "cosim_dt": float(cosim_dt),
        "tx_tds_step": float(tx_tds_step),
        "state": state,
        "updated": int(updated),
        "P_total": float(total_p),
        "Q_total": float(total_q),
        "Vmag": float(vmag),
        "Vang_rad": float(vang_rad),
    }
    if diagnostics:
        row.update(diagnostics)
    return row


def write_transmission_timeseries(csv_path, rows):
    if rows:
        df = pd.DataFrame(rows, columns=TRANSMISSION_CSV_COLUMNS)
    else:
        df = pd.DataFrame(columns=TRANSMISSION_CSV_COLUMNS)
    df.to_csv(csv_path, index=False)
    print(f"Transmission: saved timeseries CSV to {csv_path}")

SCRIPT_DIR = Path(__file__).resolve().parent
CASE_XLSX = get_transmission_case_path(SCRIPT_DIR)
TRANSMISSION_CSV_PATH = SCRIPT_DIR / "transmission_timeseries.csv"
BROKER_URL = get_broker_url()
VOLTAGE_TOPIC = get_voltage_topic()
INTERFACE_BUS = get_interface_bus()
TRANSMISSION_ALERT_CSV_PATH = SCRIPT_DIR / f"bus{INTERFACE_BUS}_fidvr_alerts.csv"
FEEDER_COUNT = get_feeder_count()
COSIM_BASE_MVA = get_cosim_base_mva()
HELICS_UNINTERRUPTIBLE = get_env_bool("HELICS_UNINTERRUPTIBLE", False)
DISTURBANCE = get_disturbance_config()
KEEP_BUILTIN_EVENTS = get_env_bool("TX_KEEP_BUILTIN_EVENTS", False)
fine_dt, coarse_dt, coarse_start = get_cosim_step_config(DISTURBANCE)
TDS_INTERNAL_STEP = get_tds_internal_step(fine_dt)

if not CASE_XLSX.exists():
    raise FileNotFoundError(f"Transmission case not found: {CASE_XLSX}")

# 1. HELICS Federate setup
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
trans_fed = h.helicsCreateValueFederate("TransmissionFed", fedinfo)

pubV = h.helicsFederateRegisterGlobalPublication(
    trans_fed, VOLTAGE_TOPIC, h.HELICS_DATA_TYPE_COMPLEX, ""
)
h.helicsPublicationSetOption(pubV, h.HELICS_HANDLE_OPTION_ONLY_TRANSMIT_ON_CHANGE, 1)

feeder_subs = []
for i in range(1, FEEDER_COUNT + 1):
    feeder_subs.append(
        h.helicsFederateRegisterSubscription(
            trans_fed, get_feeder_power_topic(i), ""
        )
    )

print("Transmission: HELICS interfaces created.")
print(
    "Transmission config: "
    f"case={CASE_XLSX} interface_bus={INTERFACE_BUS} feeders={FEEDER_COUNT} "
    f"interface_base={COSIM_BASE_MVA:.3f} MVA broker={BROKER_URL} "
    f"voltage_topic={VOLTAGE_TOPIC} "
    f"uninterruptible={HELICS_UNINTERRUPTIBLE}"
)

# 2. Load ANDES
ss = andes.load(str(CASE_XLSX), setup=False, default_config=True)
tx_system_base_mva = float(ss.config.mva)
power_scale = COSIM_BASE_MVA / tx_system_base_mva
if not KEEP_BUILTIN_EVENTS:
    disable_built_in_disturbances(ss)
print(
    "Transmission base alignment: "
    f"interface_base={COSIM_BASE_MVA:.3f} MVA, "
    f"andes_base={tx_system_base_mva:.3f} MVA, "
    f"interface_to_system_scale={power_scale:.6f}"
)
print(
    "Transmission embedded workbook events: "
    f"{'kept' if KEEP_BUILTIN_EVENTS else 'disabled'}"
)
ss.PQ.add(idx="DistLoad", name="DistLoad", bus=INTERFACE_BUS, p0=0.0, q0=0.0)

if DISTURBANCE["enabled"]:
    ss.add(
        "Fault",
        {
            "idx": DISTURBANCE["fault_idx"],
            "bus": DISTURBANCE["fault_bus"],
            "tf": DISTURBANCE["fault_time"],
            "tc": DISTURBANCE["fault_clear_time"],
            "rf": DISTURBANCE["fault_rf"],
            "xf": DISTURBANCE["fault_xf"],
        },
    )
    print(
        "Transmission disturbance: "
        f"3-phase bus fault on bus {DISTURBANCE['fault_bus']} "
        f"at t={DISTURBANCE['fault_time']:.3f}s "
        f"for {DISTURBANCE['fault_duration']:.3f}s "
        f"(rf={DISTURBANCE['fault_rf']:.6g}, xf={DISTURBANCE['fault_xf']:.6g})"
    )

    if DISTURBANCE["postfault_line_enabled"]:
        for line_idx in DISTURBANCE["line_indices"]:
            ss.add(
                "Toggle",
                {
                    "idx": f"PostFaultTrip_{line_idx}",
                    "model": "Line",
                    "dev": line_idx,
                    "t": DISTURBANCE["postfault_trip_time"],
                },
            )
        secondary_lines = ", ".join(DISTURBANCE["line_indices"])
        print(
            f"Transmission secondary event: line trip on {secondary_lines} "
            f"at t={DISTURBANCE['postfault_trip_time']:.3f}s (after fault clears)"
        )


# Constant-power behavior during TDS
ss.PQ.config.p2p = 1.0
ss.PQ.config.p2i = 0.0
ss.PQ.config.p2z = 0.0
ss.PQ.config.q2q = 1.0
ss.PQ.config.q2i = 0.0
ss.PQ.config.q2z = 0.0

ss.setup()
ss.PFlow.run()

fault_uid = None
fault_bus_uid = None
try:
    fault_uid = ss.Fault.idx2uid(DISTURBANCE["fault_idx"])
    fault_bus_uid = ss.Bus.idx2uid(DISTURBANCE["fault_bus"])
except Exception as exc:
    if DISTURBANCE["enabled"]:
        raise RuntimeError(
            f"Transmission fault '{DISTURBANCE['fault_idx']}' on bus "
            f"{DISTURBANCE['fault_bus']} could not be resolved."
        ) from exc
    print(
        "Transmission: warning: "
        f"could not resolve configured fault '{DISTURBANCE['fault_idx']}': {exc}"
    )

line_uid = None
line_bus1 = None
line_bus2 = None
line_bus1_uid = None
line_bus2_uid = None

if DISTURBANCE["line_idx"] is not None:
    try:
        line_uid = ss.Line.idx2uid(DISTURBANCE["line_idx"])
        line_bus1 = int(ss.Line.bus1.v[line_uid])
        line_bus2 = int(ss.Line.bus2.v[line_uid])
        line_bus1_uid = ss.Bus.idx2uid(line_bus1)
        line_bus2_uid = ss.Bus.idx2uid(line_bus2)
    except Exception as exc:
        print(
            "Transmission: warning: "
            f"could not resolve monitored line '{DISTURBANCE['line_idx']}': {exc}"
        )

if line_uid is not None:
    print(
        f"Transmission post-fault monitored line: {DISTURBANCE['line_idx']} "
        f"({line_bus1}-{line_bus2})"
    )
if DISTURBANCE["enabled"]:
    print(
        f"Transmission primary disturbance: bus fault at bus "
        f"{DISTURBANCE['fault_bus']} from t={DISTURBANCE['fault_time']:.3f}s "
        f"to t={DISTURBANCE['fault_clear_time']:.3f}s"
    )
else:
    print("Transmission disturbance: disabled for baseline run.")
if abs(fine_dt - coarse_dt) < 1e-12:
    print(f"Transmission: co-simulation step schedule = constant {fine_dt:.3f}s")
else:
    print(
        "Transmission: co-simulation step schedule = "
        f"{fine_dt:.3f}s until t={coarse_start:.3f}s, then {coarse_dt:.3f}s"
    )

interface_bus_uid = ss.Bus.idx2uid(INTERFACE_BUS)
last_s = [0.0 + 0.0j] * len(feeder_subs)

# ---- HELICS initialization handshake at t = 0 ----
max_init = 20
tol_init_v = 1e-6
tol_init_s = 1e-6

interface_voltage_complex, Vmag, V_angle_rad = get_tx_bus_voltage(ss, interface_bus_uid)
V_prev = interface_voltage_complex
S_prev = None

h.helicsFederateEnterInitializingMode(trans_fed)
print("Transmission: entered HELICS initialization mode.")

# Publish the initial transmission voltage guess before the first iterative entry
h.helicsPublicationPublishComplex(pubV, interface_voltage_complex)

for k in range(max_init):
    init_state = h.helicsFederateEnterExecutingModeIterative(
        trans_fed, h.HELICS_ITERATION_REQUEST_ITERATE_IF_NEEDED
    )

    total_P_raw, total_Q_raw, updated_cnt = get_total_feeder_power(feeder_subs, last_s)
    total_P, total_Q = scale_power_to_system_base(
        total_P_raw,
        total_Q_raw,
        COSIM_BASE_MVA,
        tx_system_base_mva,
    )

    # During the initialization handshake, update the static PF load
    ss.PQ.alter("p0", "DistLoad", total_P)
    ss.PQ.alter("q0", "DistLoad", total_Q)
    ss.PFlow.run()

    interface_voltage_new, Vmag_new, V_angle_rad_new = get_tx_bus_voltage(
        ss, interface_bus_uid
    )
    S_new = complex(total_P, total_Q)

    dV = abs(interface_voltage_new - V_prev)
    dS = float("inf") if S_prev is None else abs(S_new - S_prev)

    print(
        f"[TX init {k+1:02d}] updated={updated_cnt}/{len(feeder_subs)} "
        f"P={total_P:.6f} Q={total_Q:.6f} "
        f"(interface={total_P_raw:.6f}+j{total_Q_raw:.6f} pu) "
        f"|V|={Vmag_new:.6f} ang={V_angle_rad_new:.6f} "
        f"dV={dV:.3e} dS={dS:.3e}"
    )

    # Publish only if our output changed meaningfully
    if dV > tol_init_v or dS > tol_init_s:
        h.helicsPublicationPublishComplex(pubV, interface_voltage_new)

    V_prev = interface_voltage_new
    S_prev = S_new

    if init_state == h.HELICS_ITERATION_RESULT_NEXT_STEP:
        break
else:
    raise RuntimeError("Transmission initialization handshake did not converge.")

print("Transmission: initialization handshake converged.")

# ---- NOW initialize TDS from the converged static equilibrium ----
ss.TDS.config.tstep = TDS_INTERNAL_STEP
ss.TDS.config.tf = 0.0

# Adjust solver tolerances for fault event handling
if DISTURBANCE["enabled"]:
    # Relax tolerances during fault to help convergence (DAE tolerance
    # is not exposed on ss.dae in this ANDES API; adjust TDS tolerances instead)
    ss.TDS.config.atol = 1e-6
    ss.TDS.config.rtol = 1e-4
    # Limit minimum time step to prevent excessive reduction
    ss.TDS.config.min_tstep = 0.0001  # Allow down to 0.1 ms steps
    
ss.TDS.run()

interface_voltage_complex, Vmag, V_angle_rad = get_tx_bus_voltage(ss, interface_bus_uid)
Vprev = interface_voltage_complex

print(f"Interface bus {INTERFACE_BUS} initial voltage: {interface_voltage_complex:.6f}")
print(f"Interface bus {INTERFACE_BUS} initial voltage mag: {Vmag:.6f}")
print(f"Interface bus {INTERFACE_BUS} initial voltage angle: {V_angle_rad:.6f}")

# Optional: print the now-consistent initialized DistLoad
uid = ss.PQ.idx2uid("DistLoad")
print(f"DistLoad p0={ss.PQ.p0.v[uid]:.6f}, q0={ss.PQ.q0.v[uid]:.6f}")

timeseries_rows = [
    make_timeseries_row(
        iteration=0,
        outer_iter=0,
        t_granted=0.0,
        cosim_dt=0.0,
        tx_tds_step=TDS_INTERNAL_STEP,
        state="INITIALIZED",
        updated=len(feeder_subs),
        total_p=ss.PQ.p0.v[uid],
        total_q=ss.PQ.q0.v[uid],
        vmag=Vmag,
        vang_rad=V_angle_rad,
        diagnostics={
            **get_fault_diagnostics(
                ss,
                fault_uid,
                DISTURBANCE["fault_idx"],
                DISTURBANCE["fault_bus"],
                fault_bus_uid,
                DISTURBANCE,
            ),
            **get_event_line_diagnostics(
                ss,
                line_uid,
                DISTURBANCE["line_idx"],
                line_bus1,
                line_bus2,
                line_bus1_uid,
                line_bus2_uid,
            ),
            **get_genrou_diagnostics(ss),
        },
    )
]
tx_alert_detector = FidvrAlertDetector()
tx_alert_detector.update(0.0, Vmag)

# 3. Dynamic loop starts here, same idea as your current code
current_time = 0.0
target_time = get_target_time()
print(f"Transmission: target simulation time = {target_time:.3f} s")

iter_count = 0
tolV = 1e-5
alpha = 1
max_outer = 20
tx_failed = False
tx_failure_message = None
tx_progress_interval = 0.5
next_tx_progress_time = tx_progress_interval

try:
    while current_time < target_time and not tx_failed:
        current_dt = fine_dt if current_time + 1e-9 < coarse_start else coarse_dt
        next_time = min(current_time + current_dt, target_time)
        iter_req = h.HELICS_ITERATION_REQUEST_ITERATE_IF_NEEDED

        for k in range(max_outer):
            granted_time, iteration_state = h.helicsFederateRequestTimeIterative(
                trans_fed, next_time, iter_req
            )
            current_time = granted_time
            iter_count += 1

            total_P_raw, total_Q_raw, updated_cnt = get_total_feeder_power(
                feeder_subs, last_s
            )
            total_P, total_Q = scale_power_to_system_base(
                total_P_raw,
                total_Q_raw,
                COSIM_BASE_MVA,
                tx_system_base_mva,
            )

            # During TDS, update Ppf/Qpf, not p0/q0
            ss.PQ.set(src="Ppf", idx="DistLoad", attr="v", value=total_P)
            ss.PQ.set(src="Qpf", idx="DistLoad", attr="v", value=total_Q)

            ss.TDS.config.tf = granted_time
            ss.TDS.run(no_summary=True)

            if ss.dae.t + 1e-9 < granted_time:
                tx_failure_message = (
                    "Transmission TDS failed to reach requested time "
                    f"(dae.t={ss.dae.t:.6f}, requested={granted_time:.6f})."
                )
                print(
                    f"[TX] TDS failed to reach requested time: "
                    f"dae.t={ss.dae.t:.6f}, requested={granted_time:.6f}"
                )
                print(
                    f"[TX progress] t={ss.dae.t:.3f}s/{target_time:.3f}s "
                    f"state=FAILED updated={updated_cnt}/{len(feeder_subs)} "
                    f"P={total_P:.6f} Q={total_Q:.6f}"
                )
                tx_failed = True
                break

            interface_voltage_new, Vmag_new, V_angle_rad_new = get_tx_bus_voltage(
                ss, interface_bus_uid
            )
            interface_voltage_pub = alpha * interface_voltage_new + (1 - alpha) * Vprev

            h.helicsPublicationPublishComplex(pubV, interface_voltage_pub)

            if abs(interface_voltage_pub - Vprev) < tolV:
                iter_req = h.HELICS_ITERATION_REQUEST_NO_ITERATION
            else:
                iter_req = h.HELICS_ITERATION_REQUEST_ITERATE_IF_NEEDED

            Vprev = interface_voltage_pub
            state_name = ITER_STATE_NAME.get(iteration_state, str(iteration_state))

            if iteration_state != h.HELICS_ITERATION_RESULT_ITERATING:
                timeseries_rows.append(
                    make_timeseries_row(
                        iteration=iter_count,
                        outer_iter=k + 1,
                        t_granted=granted_time,
                        cosim_dt=current_dt,
                        tx_tds_step=TDS_INTERNAL_STEP,
                        state=state_name,
                        updated=updated_cnt,
                        total_p=total_P,
                        total_q=total_Q,
                        vmag=Vmag_new,
                        vang_rad=V_angle_rad_new,
                        diagnostics={
                            **get_fault_diagnostics(
                                ss,
                                fault_uid,
                                DISTURBANCE["fault_idx"],
                                DISTURBANCE["fault_bus"],
                                fault_bus_uid,
                                DISTURBANCE,
                            ),
                            **get_event_line_diagnostics(
                                ss,
                                line_uid,
                                DISTURBANCE["line_idx"],
                                line_bus1,
                                line_bus2,
                                line_bus1_uid,
                                line_bus2_uid,
                            ),
                            **get_genrou_diagnostics(ss),
                        },
                    )
                )
                for alert in tx_alert_detector.update(granted_time, Vmag_new):
                    print(
                        f"[TX ALERT] t={alert.trigger_time_s:.3f}s "
                        f"{alert.alert_id} {alert.alert_name} "
                        f"V={alert.trigger_voltage_pu:.6f} pu | {alert.details}"
                    )
                if (
                    granted_time + 1e-9 >= next_tx_progress_time
                    or granted_time + 1e-9 >= target_time
                    or iteration_state in (
                        h.HELICS_ITERATION_RESULT_ERROR,
                        h.HELICS_ITERATION_RESULT_HALTED,
                    )
                ):
                    print(
                        f"[TX progress] t={granted_time:.3f}s/{target_time:.3f}s "
                        f"state={state_name} updated={updated_cnt}/{len(feeder_subs)} "
                        f"P={total_P:.6f} Q={total_Q:.6f} "
                        f"(interface={total_P_raw:.6f}+j{total_Q_raw:.6f} pu) "
                        f"|V|={Vmag_new:.6f} ang={V_angle_rad_new:.6f} "
                        f"dt={current_dt:.3f}s"
                    )
                    while next_tx_progress_time <= granted_time + 1e-9:
                        next_tx_progress_time += tx_progress_interval
                if iteration_state in (
                    h.HELICS_ITERATION_RESULT_ERROR,
                    h.HELICS_ITERATION_RESULT_HALTED,
                ):
                    tx_failed = True
                    tx_failure_message = (
                        "Transmission HELICS iteration terminated with "
                        f"state={state_name} at t={granted_time:.3f}s."
                    )
                break
        else:
            print(f"[TX] WARN: hit max_outer={max_outer} at t={current_time:.3f}s")
            tx_failed = True
            tx_failure_message = (
                f"Transmission hit max_outer={max_outer} at t={current_time:.3f}s."
            )
finally:
    write_transmission_timeseries(TRANSMISSION_CSV_PATH, timeseries_rows)
    tx_alerts = tx_alert_detector.to_dataframe()
    tx_alerts.to_csv(TRANSMISSION_ALERT_CSV_PATH, index=False)
    print(f"Transmission: saved alert CSV to {TRANSMISSION_ALERT_CSV_PATH}")
    for line in alert_summary_lines(tx_alerts, f"Bus {INTERFACE_BUS} |V|"):
        print(f"[TX ALERT SUMMARY] {line}")
    try:
        h.helicsFederateDisconnect(trans_fed)
    except Exception as exc:
        print(f"Transmission: HELICS disconnect warning: {exc}")
    print("Transmission: Finished.")

if tx_failure_message is not None:
    raise RuntimeError(tx_failure_message)
