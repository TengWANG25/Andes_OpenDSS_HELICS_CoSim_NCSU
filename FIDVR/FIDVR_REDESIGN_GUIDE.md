# FIDVR Co-Simulation Guide

This repository now has a single FIDVR motor path: the WECC/LD1PAC Motor D
implementation in `Distribution.py`. The old staged surrogate, companion-load,
and `IndMach012` experiment paths have been removed from the active run path so
the study is easier to explain and audit.

## Current Framework

- Transmission side: ANDES applies the selected temporary bus fault, optionally
  opens selected post-fault lines after clearing, and publishes the interface-bus
  voltage to HELICS.
- Distribution side: OpenDSS receives the interface voltage, solves the feeder,
  and reports aggregate feeder P/Q plus monitored distribution-bus voltage.
- Motor model: each selected single-phase OpenDSS load is split into a static
  portion and one `Load.weccmd_*` Motor D terminal injection. The same terminal
  injection remains in place through running, stall, undervoltage contactor
  dropout, thermal trip, restart, and thermal load restoration.
- Feeder controls: capacitor and regulator controls are optional. They are
  disabled unless their `FIDVR_ENABLE_*_CONTROL` variable is set.

## Recommended Runs

Baseline fault-only check:

```bash
FIDVR_ENABLE=0 SIM_TARGET_TIME=4.0 ./run.sh
```

Motor D only, with feeder controls frozen:

```bash
FIDVR_ENABLE=1 FIDVR_PROFILE=weak_bus14 \
FIDVR_ENABLE_CAP_CONTROL=0 FIDVR_ENABLE_REG_CONTROL=0 \
SIM_TARGET_TIME=60.0 ./run.sh
```

Second-half FIDVR controls, including capacitor lockout and thermal load
restoration:

```bash
FIDVR_ENABLE=1 FIDVR_PROFILE=second_half \
FIDVR_ENABLE_THERMAL_LOAD_RESTORATION=1 \
SIM_TARGET_TIME=120.0 ./run.sh
```

## Main Parameters

Motor size and placement:

| Variable | Meaning |
| --- | --- |
| `FIDVR_MOTOR_LOADS` | OpenDSS load names converted into Motor D injections |
| `FIDVR_MOTOR_SHARE` | Fraction of each selected load represented as Motor D |
| `DIST_LOAD_SCALE` | Feeder-wide load multiplier before Motor D splitting |

WECC Motor D:

| Variable | Meaning |
| --- | --- |
| `FIDVR_WECC_COMPPF` | Compressor running power factor |
| `FIDVR_WECC_VSTALL` | Stall voltage threshold |
| `FIDVR_WECC_TSTALL` | Delay before stalled state is accepted |
| `FIDVR_WECC_RSTALL`, `FIDVR_WECC_XSTALL` | Locked-rotor equivalent impedance |
| `FIDVR_WECC_FRST`, `FIDVR_WECC_VRST`, `FIDVR_WECC_TRST` | Restart fraction, voltage, and delay |
| `FIDVR_WECC_TH1T`, `FIDVR_WECC_TH2T`, `FIDVR_WECC_TTH` | Thermal protection curve points and time constant |
| `FIDVR_WECC_UVTR1`, `FIDVR_WECC_TTR1`, `FIDVR_WECC_UVTR2`, `FIDVR_WECC_TTR2` | Undervoltage trip thresholds and delays |

Thermal load restoration:

| Variable | Meaning |
| --- | --- |
| `FIDVR_ENABLE_THERMAL_LOAD_RESTORATION` | Enables delayed reconnection after thermal trip |
| `FIDVR_THERMAL_RESTORE_MIN_DELAY_S`, `FIDVR_THERMAL_RESTORE_MAX_DELAY_S` | Reconnection delay window |
| `FIDVR_THERMAL_RESTORE_RAMP_S` | Ramp duration after reconnection begins |
| `FIDVR_THERMAL_RESTORE_VOLTAGE_PU` | Voltage needed to continue restoration |
| `FIDVR_THERMAL_RESTORE_DROPOUT_VOLTAGE_PU` | Voltage below which restoration pauses |
| `FIDVR_THERMAL_RESTORE_FRACTION` | Fraction of thermally tripped load allowed to return |

Capacitor and regulator controls:

| Variable | Meaning |
| --- | --- |
| `FIDVR_ENABLE_CAP_CONTROL` | Enables delayed capacitor on/off logic |
| `FIDVR_CAPACITOR_INITIAL_FRACTION` | Initial fraction of selected capacitor banks online |
| `FIDVR_CAPACITOR_KVAR_SCALE` | Scales selected existing capacitor kvar |
| `FIDVR_CAPACITOR_OFF_VOLTAGE_PU`, `FIDVR_CAPACITOR_OFF_DELAY_S` | Overvoltage trip rule |
| `FIDVR_CAPACITOR_ON_VOLTAGE_PU`, `FIDVR_CAPACITOR_ON_DELAY_S` | Low-voltage close rule |
| `FIDVR_CAPACITOR_LOCKOUT_AFTER_OPEN` | Keeps opened capacitors offline |
| `FIDVR_ENABLE_REG_CONTROL` | Enables delayed regulator tap control |
| `FIDVR_REGULATOR_LOW_VOLTAGE_PU`, `FIDVR_REGULATOR_HIGH_VOLTAGE_PU` | Regulator deadband limits |
| `FIDVR_REGULATOR_DELAY_S`, `FIDVR_REGULATOR_TAP_DELAY_S` | First action and subsequent tap delays |

## Validation Checklist

- Fault-only case creates a clear voltage sag and recovers quickly after clearing.
- Motor D case shows a reactive power rise during the delayed recovery interval.
- Motor logs show physically named states: running, stalled, tripped, restoring.
- Capacitor events only occur when capacitor control is enabled and the monitored
  voltage satisfies the delayed threshold logic.
- Regulator tap changes only occur when regulator control is enabled and the
  monitored voltage remains outside the deadband long enough.
- Thermal load restoration is only claimed when
  `FIDVR_ENABLE_THERMAL_LOAD_RESTORATION=1` and the log shows restoration
  fractions increasing.

## Plotting

```bash
python3 plot_distribution_from_logs.py
python3 plot_from_logs.py
```

Use `feeder_1.log`, `feeder_1_fidvr_alerts.csv`,
`feeder_1_distribution_voltage.csv`, and `transmission_timeseries.csv` to trace
the event chronology.
