#!/usr/bin/env python3
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math

import pandas as pd


DEFAULT_SYSTEM_FREQUENCY_HZ = 60.0
DEFAULT_FAULT_WINDOW_CYCLES = 3.0
DEFAULT_FAULT_DIP_PU = 0.20
DEFAULT_STALL_ALERT_VOLTAGE_PU = 0.90
DEFAULT_STALL_ALERT_DURATION_S = 5.0
DEFAULT_OVERVOLTAGE_ALERT_PU = 1.05
DEFAULT_OVERVOLTAGE_ALERT_DURATION_S = 1.0
DEFAULT_OVERVOLTAGE_LOOKAHEAD_S = 120.0

ALERT_COLORS = {
    "Alert.1": "#b2182b",
    "Alert.2": "#ef8a62",
    "Alert.3": "#2166ac",
}


@dataclass(frozen=True)
class FidvrAlertConfig:
    system_frequency_hz: float = DEFAULT_SYSTEM_FREQUENCY_HZ
    fault_window_cycles: float = DEFAULT_FAULT_WINDOW_CYCLES
    fault_dip_pu: float = DEFAULT_FAULT_DIP_PU
    stall_voltage_pu: float = DEFAULT_STALL_ALERT_VOLTAGE_PU
    stall_duration_s: float = DEFAULT_STALL_ALERT_DURATION_S
    overvoltage_pu: float = DEFAULT_OVERVOLTAGE_ALERT_PU
    overvoltage_duration_s: float = DEFAULT_OVERVOLTAGE_ALERT_DURATION_S
    overvoltage_lookahead_s: float = DEFAULT_OVERVOLTAGE_LOOKAHEAD_S

    @property
    def fault_window_s(self) -> float:
        return self.fault_window_cycles / self.system_frequency_hz


@dataclass
class FidvrAlertEvent:
    alert_id: str
    alert_name: str
    criterion: str
    threshold_pu: float
    window_s: float
    triggered: bool = False
    reference_voltage_pu: float = math.nan
    trigger_time_s: float = math.nan
    trigger_voltage_pu: float = math.nan
    trigger_voltage_norm_pu: float = math.nan
    start_time_s: float = math.nan
    end_time_s: float = math.nan
    duration_s: float = math.nan
    details: str = ""

    def as_row(self) -> dict[str, object]:
        return {
            "alert_id": self.alert_id,
            "alert_name": self.alert_name,
            "criterion": self.criterion,
            "threshold_pu": self.threshold_pu,
            "window_s": self.window_s,
            "triggered": self.triggered,
            "reference_voltage_pu": self.reference_voltage_pu,
            "trigger_time_s": self.trigger_time_s,
            "trigger_voltage_pu": self.trigger_voltage_pu,
            "trigger_voltage_norm_pu": self.trigger_voltage_norm_pu,
            "start_time_s": self.start_time_s,
            "end_time_s": self.end_time_s,
            "duration_s": self.duration_s,
            "details": self.details,
        }


def _base_alert_event(
    alert_id: str,
    alert_name: str,
    criterion: str,
    threshold_pu: float,
    window_s: float,
) -> FidvrAlertEvent:
    return FidvrAlertEvent(
        alert_id=alert_id,
        alert_name=alert_name,
        criterion=criterion,
        threshold_pu=threshold_pu,
        window_s=window_s,
    )


class FidvrAlertDetector:
    def __init__(
        self,
        config: FidvrAlertConfig | None = None,
        reference_voltage_pu: float | None = None,
    ):
        self.config = config or FidvrAlertConfig()
        self.reference_voltage_pu = (
            float(reference_voltage_pu)
            if reference_voltage_pu is not None and math.isfinite(reference_voltage_pu)
            else None
        )
        self._recent_samples: deque[tuple[float, float]] = deque()
        self._undervoltage_started_at: float | None = None
        self._overvoltage_started_at: float | None = None
        self._last_time_s: float | None = None
        self._alerts = {
            "Alert.1": _base_alert_event(
                "Alert.1",
                "Fault Alert",
                (
                    f"Voltage dip >= {self.config.fault_dip_pu:.2f} pu "
                    f"within {self.config.fault_window_cycles:.1f} cycles"
                ),
                self.config.fault_dip_pu,
                self.config.fault_window_s,
            ),
            "Alert.2": _base_alert_event(
                "Alert.2",
                "Air Conditioner Stall Alert",
                (
                    f"Voltage <= {self.config.stall_voltage_pu:.2f} pu "
                    f"for >= {self.config.stall_duration_s:.1f} s"
                ),
                self.config.stall_voltage_pu,
                self.config.stall_duration_s,
            ),
            "Alert.3": _base_alert_event(
                "Alert.3",
                "Overvoltage Alert",
                (
                    f"Voltage >= {self.config.overvoltage_pu:.2f} pu "
                    f"for >= {self.config.overvoltage_duration_s:.1f} s "
                    f"within {self.config.overvoltage_lookahead_s:.0f} s of Alert.2"
                ),
                self.config.overvoltage_pu,
                self.config.overvoltage_duration_s,
            ),
        }

    def update(self, time_s: float, voltage_pu: float) -> list[FidvrAlertEvent]:
        if not math.isfinite(time_s) or not math.isfinite(voltage_pu):
            return []
        if self._last_time_s is not None and time_s + 1e-12 < self._last_time_s:
            raise ValueError(
                "FIDVR alert detector received non-monotonic time samples."
            )
        self._last_time_s = time_s
        if self.reference_voltage_pu is None:
            self.reference_voltage_pu = voltage_pu
        reference_voltage_pu = max(self.reference_voltage_pu, 1e-9)
        voltage_norm_pu = voltage_pu / reference_voltage_pu

        new_alerts = []
        self._recent_samples.append((time_s, voltage_norm_pu))
        recent_cutoff = time_s - self.config.fault_window_s - 1e-12
        while self._recent_samples and self._recent_samples[0][0] < recent_cutoff:
            self._recent_samples.popleft()

        new_alert = self._detect_fault_alert(time_s, voltage_pu, voltage_norm_pu)
        if new_alert is not None:
            new_alerts.append(new_alert)

        new_alert = self._detect_stall_alert(time_s, voltage_pu, voltage_norm_pu)
        if new_alert is not None:
            new_alerts.append(new_alert)

        new_alert = self._detect_overvoltage_alert(time_s, voltage_pu, voltage_norm_pu)
        if new_alert is not None:
            new_alerts.append(new_alert)

        return new_alerts

    def _detect_fault_alert(
        self, time_s: float, voltage_pu: float, voltage_norm_pu: float
    ) -> FidvrAlertEvent | None:
        alert = self._alerts["Alert.1"]
        if alert.triggered or not self._recent_samples:
            return None

        reference_time_s, reference_voltage_norm_pu = max(
            self._recent_samples, key=lambda sample: sample[1]
        )
        dip_pu = reference_voltage_norm_pu - voltage_norm_pu
        duration_s = max(0.0, time_s - reference_time_s)
        if dip_pu + 1e-12 < self.config.fault_dip_pu:
            return None

        alert.triggered = True
        alert.reference_voltage_pu = float(self.reference_voltage_pu)
        alert.start_time_s = reference_time_s
        alert.end_time_s = time_s
        alert.trigger_time_s = time_s
        alert.trigger_voltage_pu = voltage_pu
        alert.trigger_voltage_norm_pu = voltage_norm_pu
        alert.duration_s = duration_s
        alert.details = (
            f"normalized drop={dip_pu:.3f} pu from {reference_voltage_norm_pu:.3f} pu "
            f"over {duration_s:.3f} s (ref={self.reference_voltage_pu:.3f} pu)"
        )
        return alert

    def _detect_stall_alert(
        self, time_s: float, voltage_pu: float, voltage_norm_pu: float
    ) -> FidvrAlertEvent | None:
        alert = self._alerts["Alert.2"]
        if voltage_norm_pu <= self.config.stall_voltage_pu:
            if self._undervoltage_started_at is None:
                self._undervoltage_started_at = time_s
        else:
            self._undervoltage_started_at = None
            return None

        if alert.triggered or self._undervoltage_started_at is None:
            return None

        duration_s = max(0.0, time_s - self._undervoltage_started_at)
        if duration_s + 1e-12 < self.config.stall_duration_s:
            return None

        alert.triggered = True
        alert.reference_voltage_pu = float(self.reference_voltage_pu)
        alert.start_time_s = self._undervoltage_started_at
        alert.end_time_s = time_s
        alert.trigger_time_s = self._undervoltage_started_at + self.config.stall_duration_s
        alert.trigger_voltage_pu = voltage_pu
        alert.trigger_voltage_norm_pu = voltage_norm_pu
        alert.duration_s = duration_s
        alert.details = (
            f"normalized voltage stayed below {self.config.stall_voltage_pu:.2f} pu "
            f"for {duration_s:.3f} s (ref={self.reference_voltage_pu:.3f} pu)"
        )
        return alert

    def _detect_overvoltage_alert(
        self, time_s: float, voltage_pu: float, voltage_norm_pu: float
    ) -> FidvrAlertEvent | None:
        stall_alert = self._alerts["Alert.2"]
        alert = self._alerts["Alert.3"]

        if not stall_alert.triggered:
            self._overvoltage_started_at = None
            return None

        window_end_s = stall_alert.trigger_time_s + self.config.overvoltage_lookahead_s
        if time_s > window_end_s + 1e-12:
            self._overvoltage_started_at = None
            return None

        if voltage_norm_pu >= self.config.overvoltage_pu:
            if self._overvoltage_started_at is None:
                self._overvoltage_started_at = time_s
        else:
            self._overvoltage_started_at = None
            return None

        if alert.triggered or self._overvoltage_started_at is None:
            return None

        duration_s = max(0.0, time_s - self._overvoltage_started_at)
        if duration_s + 1e-12 < self.config.overvoltage_duration_s:
            return None

        alert.triggered = True
        alert.reference_voltage_pu = float(self.reference_voltage_pu)
        alert.start_time_s = self._overvoltage_started_at
        alert.end_time_s = time_s
        alert.trigger_time_s = self._overvoltage_started_at + self.config.overvoltage_duration_s
        alert.trigger_voltage_pu = voltage_pu
        alert.trigger_voltage_norm_pu = voltage_norm_pu
        alert.duration_s = duration_s
        alert.details = (
            f"normalized voltage stayed above {self.config.overvoltage_pu:.2f} pu "
            f"for {duration_s:.3f} s (ref={self.reference_voltage_pu:.3f} pu)"
        )
        return alert

    def to_dataframe(self) -> pd.DataFrame:
        ordered_alerts = [self._alerts[key].as_row() for key in ("Alert.1", "Alert.2", "Alert.3")]
        return pd.DataFrame(ordered_alerts)


def detect_fidvr_alerts(
    times_s,
    voltages_pu,
    config: FidvrAlertConfig | None = None,
    reference_voltage_pu: float | None = None,
) -> pd.DataFrame:
    detector = FidvrAlertDetector(
        config=config, reference_voltage_pu=reference_voltage_pu
    )
    for time_s, voltage_pu in zip(times_s, voltages_pu):
        detector.update(float(time_s), float(voltage_pu))
    return detector.to_dataframe()


def alert_summary_lines(
    alerts: pd.DataFrame,
    signal_label: str = "Voltage signal",
) -> list[str]:
    lines = []
    for row in alerts.itertuples(index=False):
        if bool(row.triggered):
            lines.append(
                f"{signal_label} {row.alert_id} {row.alert_name}: "
                f"triggered at t={row.trigger_time_s:.3f}s, "
                f"V={row.trigger_voltage_pu:.4f} pu "
                f"(normalized={row.trigger_voltage_norm_pu:.4f} pu). "
                f"{row.details}"
            )
        else:
            lines.append(
                f"{signal_label} {row.alert_id} {row.alert_name}: not triggered."
            )
    return lines
