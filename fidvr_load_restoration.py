from __future__ import annotations

from dataclasses import dataclass


def _clip_fraction(value: float) -> float:
    return max(0.0, min(1.0, value))


@dataclass(frozen=True)
class ThermalLoadRestorationConfig:
    enabled: bool = False
    restore_delay_s: float = 180.0
    restore_ramp_s: float = 30.0
    restore_voltage_pu: float = 0.90
    dropout_voltage_pu: float = 0.70
    restore_fraction: float = 1.0


@dataclass(frozen=True)
class ThermalLoadRestorationState:
    trip_time_s: float | None = None
    restore_started_at_s: float | None = None
    restored_fraction: float = 0.0
    target_fraction: float = 0.0


def update_thermal_load_restoration(
    state: ThermalLoadRestorationState,
    config: ThermalLoadRestorationConfig,
    *,
    thermal_trip_fraction: float,
    voltage_pu: float,
    current_time_s: float,
) -> ThermalLoadRestorationState:
    """Return restored load fraction after delayed thermal-reset restoration.

    This represents the slower post-FIDVR return of compressor load after
    thermal protection or customer controls reset. It is intentionally separate
    from WECC LD1PAC's short Frst/Vrst/Trst restart path.
    """
    thermal_trip_fraction = _clip_fraction(thermal_trip_fraction)
    restore_cap = _clip_fraction(config.restore_fraction)

    if not config.enabled or thermal_trip_fraction <= 1e-9 or restore_cap <= 1e-9:
        return ThermalLoadRestorationState()

    target_fraction = max(
        _clip_fraction(state.target_fraction),
        _clip_fraction(restore_cap * thermal_trip_fraction),
    )
    trip_time_s = (
        current_time_s if state.trip_time_s is None else float(state.trip_time_s)
    )

    if voltage_pu < config.dropout_voltage_pu:
        return ThermalLoadRestorationState(
            trip_time_s=current_time_s,
            restore_started_at_s=None,
            restored_fraction=0.0,
            target_fraction=target_fraction,
        )

    restored_fraction = min(_clip_fraction(state.restored_fraction), target_fraction)
    restore_started_at_s = state.restore_started_at_s
    ready_time_s = trip_time_s + max(0.0, config.restore_delay_s)

    if current_time_s + 1e-9 >= ready_time_s and voltage_pu >= config.restore_voltage_pu:
        if restore_started_at_s is None:
            restore_started_at_s = current_time_s
        elapsed_s = max(0.0, current_time_s - restore_started_at_s)
        if config.restore_ramp_s <= 1e-9:
            ramp_fraction = 1.0
        else:
            ramp_fraction = _clip_fraction(elapsed_s / config.restore_ramp_s)
        restored_fraction = max(restored_fraction, target_fraction * ramp_fraction)

    return ThermalLoadRestorationState(
        trip_time_s=trip_time_s,
        restore_started_at_s=restore_started_at_s,
        restored_fraction=_clip_fraction(restored_fraction),
        target_fraction=target_fraction,
    )
