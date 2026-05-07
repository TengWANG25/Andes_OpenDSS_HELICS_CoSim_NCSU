from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DelayedShuntControlConfig:
    on_voltage_pu: float
    off_voltage_pu: float
    on_delay_s: float
    off_delay_s: float
    hysteresis_pu: float = 0.01
    lockout_after_open: bool = False


@dataclass(frozen=True)
class DelayedShuntControlState:
    enabled: bool
    on_armed_since_s: float | None = None
    off_armed_since_s: float | None = None
    locked_out: bool = False
    last_action: str = ""


def shunt_status_from_fraction(fraction_enabled: float) -> str:
    if fraction_enabled <= 0.01:
        return "off"
    if fraction_enabled >= 0.99:
        return "on"
    return "partial"


def update_delayed_shunt_control(
    state: DelayedShuntControlState,
    config: DelayedShuntControlConfig,
    monitored_voltage_pu: float,
    current_time_s: float,
) -> DelayedShuntControlState:
    """Update a switched shunt controller with voltage thresholds and delays."""
    enabled = state.enabled
    on_armed_since_s = state.on_armed_since_s
    off_armed_since_s = state.off_armed_since_s
    locked_out = state.locked_out
    last_action = ""

    if enabled:
        on_armed_since_s = None
        if monitored_voltage_pu >= config.off_voltage_pu:
            if off_armed_since_s is None:
                off_armed_since_s = current_time_s
            elif current_time_s - off_armed_since_s >= config.off_delay_s:
                enabled = False
                locked_out = config.lockout_after_open
                off_armed_since_s = None
                last_action = "opened"
        elif monitored_voltage_pu <= config.off_voltage_pu - config.hysteresis_pu:
            off_armed_since_s = None
    elif locked_out:
        on_armed_since_s = None
        off_armed_since_s = None
    else:
        off_armed_since_s = None
        if monitored_voltage_pu <= config.on_voltage_pu:
            if on_armed_since_s is None:
                on_armed_since_s = current_time_s
            elif current_time_s - on_armed_since_s >= config.on_delay_s:
                enabled = True
                on_armed_since_s = None
                last_action = "closed"
        elif monitored_voltage_pu >= config.on_voltage_pu + config.hysteresis_pu:
            on_armed_since_s = None

    return DelayedShuntControlState(
        enabled=enabled,
        on_armed_since_s=on_armed_since_s,
        off_armed_since_s=off_armed_since_s,
        locked_out=locked_out,
        last_action=last_action,
    )
