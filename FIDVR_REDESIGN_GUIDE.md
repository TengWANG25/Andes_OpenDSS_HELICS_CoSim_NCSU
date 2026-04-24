# FIDVR Co-Simulation Redesign Guide

## Overview

This guide explains the redesigned FIDVR co-simulation framework that replaces line-trip-first scenarios with **fault-first scenarios** to create realistic voltage sag and delayed motor recovery.

### What Changed

**Before:**
- Initiated by line trip (slow, weak event)
- Fixed 0.03s co-simulation step (too coarse for fault dynamics)
- Multiple feeders by default (harder to validate)
- Scripted load stages (hard to debug motor physics)

**After:**
- Initiated by temporary fault at interface bus (fast, realistic event)
- Fine time stepping: 0.005s around fault, 0.02s after recovery (captures dynamics)
- Default 1 feeder for validation (simplifies debugging)
- Improved motor model defaults (stronger Q surge, longer recovery)

---

## Test Cases A–D

Run each case in sequence to validate the motor physics before adding complexity.

### Case A: Baseline (Fault Only, No Motors)
**Purpose:** Verify transmission-side voltage sag and recovery.

```bash
FIDVR_ENABLE=0 SIM_TARGET_TIME=4.0 bash run.sh
```

**Expected Results:**
- Interface bus voltage drops sharply at t=1.0s
- Recovers within ~0.5s after fault clears (t=1.06s)
- No feeder interaction; flat Q (only network losses)

**Plot Check:** `transmission_timeseries.csv`
- Look for sharp dip in `Vmag` at t=1.0s, recovery by t~1.2s

---

### Case B: Fault + Motors (Controls Frozen)
**Purpose:** Isolate motor-driven delayed recovery from control actions.

```bash
FIDVR_ENABLE=1 DIST_LOAD_SCALE=2.0 SIM_TARGET_TIME=10.0 bash run.sh
```

**Expected Results:**
- Interface voltage dips at t=1.0s (same as Case A)
- Feeder motor bus dips *deeper* than interface (Q surge from stalled load)
- Voltage stays depressed for 3–5 seconds (thermal trip / reconnect delays)
- Q_total rises during stall, then falls as motors reconnect

**Plot Check:** `transmission_timeseries.csv` + `feeder_1_fidvr_alerts.csv`
- `Vmag` at t=1.0–3.0s: interface recovers faster than motor load recovers
- `Q_total`: peak 0.5–2s after fault, then decay over 5–10s
- Motor alerts: stall at t~1.06s, thermal trip at t~17s, reconnect at t~20s

**Key Insight:** If Q does NOT rise during stall → motor model not active or load too small.

---

### Case C: Fault + Motors + Controls (Regulators & Capacitors Active)
**Purpose:** Add feeder voltage support; see how controls modify recovery.

```bash
FIDVR_ENABLE=1 DIST_LOAD_SCALE=2.0 SIM_TARGET_TIME=10.0 bash run.sh
```

**Expected Results:**
- Same sag as Cases A–B at t=1.0s
- Capacitors switch ON during low voltage (0.5–2s after fault)
- Regulators tap up to raise feeder voltage (2–5s phase)
- Recovery may be faster or overshoot slightly (depending on control tuning)
- Delayed recovery still visible (motor holds it down)

**Plot Check:** `feeder_1_fidvr_alerts.csv`
- Capacitor switch events (~1–2s)
- Regulator tap changes (~0.5–3s window)
- Motor stall/thermal/reconnect timeline

---

### Case D: Fault + Secondary Line Outage (Later Stress)
**Purpose:** Demonstrate that controls + motors can handle multiple stresses.

```bash
FIDVR_ENABLE=1 DIST_LOAD_SCALE=2.0 TX_DISTURBANCE_LINES="Line_3" \
SIM_TARGET_TIME=15.0 bash run.sh
```

**Expected Results:**
- Fault at t=1.0–1.06s (same as A–C)
- Recovery on track by t~2s
- Line trip event at t~1.11s (after fault clears + 50ms safety margin)
- Secondary voltage dip, faster recovery due to already-raised controls

**Note:** This case only works if Case B & C show convincing delayed recovery.

---

## Running Experiments

### Single Case (e.g., Case C)

```bash
FIDVR_ENABLE=1 DIST_LOAD_SCALE=2.0 SIM_TARGET_TIME=10.0 bash run.sh
```

### Multiple Cases (A→B→C)

```bash
# Case A: Baseline
FIDVR_ENABLE=0 SIM_TARGET_TIME=4.0 bash run.sh
mv transmission_timeseries.csv transmission_case_a.csv

# Case B: Motors
FIDVR_ENABLE=1 DIST_LOAD_SCALE=2.0 SIM_TARGET_TIME=10.0 bash run.sh
mv transmission_timeseries.csv transmission_case_b.csv
mv feeder_1_fidvr_alerts.csv feeder_1_case_b_alerts.csv

# Case C: Motors + Controls
FIDVR_ENABLE=1 DIST_LOAD_SCALE=2.0 SIM_TARGET_TIME=10.0 bash run.sh
mv transmission_timeseries.csv transmission_case_c.csv
mv feeder_1_fidvr_alerts.csv feeder_1_case_c_alerts.csv
```

---

## Plotting & Analysis

Use your existing tools to compare cases:

```bash
python plot_transmission_fault_scenarios.py
# (Plots transmission_case_*.csv side-by-side)

python plot_from_logs.py --log transmission.log
# (Shows progress over time)
```

### Key Columns to Watch

**transmission_timeseries.csv:**
- `Vmag` (interface bus voltage magnitude)
- `Vang_rad` (angle, should be stable)
- `P_total`, `Q_total` (aggregated feeder power)
- `t_granted` (co-simulation time)

**feeder_1_fidvr_alerts.csv:**
- `time` (when event occurred)
- `alert_type` (stall, thermal, reconnect, cap_switch, reg_tap_change)
- `bus_name` (which bus was affected)
- `value` (voltage or load state change)

---

## Tuning Recommendations

If results don't match expected behavior, adjust these (in run.sh or manually):

### For Stronger Motor Stall (Case B)
```bash
# Increase motor share:
FIDVR_MOTOR_SHARE=0.70 bash run.sh

# Raise reactive scaling:
FIDVR_MOTOR_STALL_KVAR_SCALE=8.0 bash run.sh

# Increase load scale:
DIST_LOAD_SCALE=3.0 bash run.sh
```

### For Faster Recovery
```bash
# Reduce thermal trip time:
FIDVR_MOTOR_THERMAL_TRIP_TIME_S=12.0 bash run.sh

# Faster reconnect:
FIDVR_MOTOR_RECONNECT_DELAY_S=5.0 bash run.sh
```

### For Slower Recovery (More Realistic FIDVR)
```bash
# Extend thermal trip:
FIDVR_MOTOR_THERMAL_TRIP_TIME_S=20.0 bash run.sh

# Delay reconnect:
FIDVR_MOTOR_RECONNECT_DELAY_S=15.0 bash run.sh
```

---

## Validation Checklist

- [ ] Case A: Fault creates clear transmission voltage dip?
- [ ] Case A: Recovery time < 1s after fault clears?
- [ ] Case B: Feeder Q rises during stall (not flat)?
- [ ] Case B: Delayed recovery visible (V stays low 3–5s)?
- [ ] Case C: Caps/regs help but don't completely override motors?
- [ ] Case D: Secondary line trip doesn't collapse system?
- [ ] No solver convergence warnings in logs?

---

## New Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `TX_FAULT_TIME` | 1.0 | Fault initiation time (s) |
| `TX_FAULT_DURATION` | 0.06 | Fault duration (s) |
| `SIM_FINE_DT` | 0.005 | Fine co-sim step (s) |
| `SIM_COARSE_DT` | 0.02 | Coarse co-sim step (s) |
| `SIM_COARSE_START` | 1.5 | When to switch to coarse step (s) |
| `TX_TDS_STEP` | auto | Transmission internal step (s) |
| `DIST_LOAD_SCALE` | 1.0 | Distribution load multiplier |
| `FIDVR_MOTOR_SHARE` | 0.65 | Motor fraction of load |
| `FIDVR_MOTOR_STALL_VOLTAGE_PU` | 0.71 | Stall threshold (pu) |
| `FIDVR_MOTOR_STALL_KVAR_SCALE` | 6.5 | Reactive multiplier during stall |
| `FIDVR_MOTOR_RECONNECT_DELAY_S` | 10.0 | Motor reconnect delay (s) |

---

## Troubleshooting

### Simulation Crashes or Times Out
- Check broker is running: `ss -lnt | grep 23406`
- Reduce `SIM_FINE_DT` or `TX_TDS_STEP` if numerical issues
- Ensure case file exists: `ls -la ieee14_fault.xlsx`

### No Motor Effect in Case B
- Increase `DIST_LOAD_SCALE` (try 3.0 or 5.0)
- Raise `FIDVR_MOTOR_SHARE` (try 0.75–0.90)
- Verify `FIDVR_ENABLE=1` is set
- Check that `feeder_1_fidvr_alerts.csv` shows stall events

### Too Fast Recovery
- Reduce `FIDVR_MOTOR_THERMAL_TRIP_TIME_S`
- Lower stall clear voltage: `FIDVR_MOTOR_STALL_CLEAR_VOLTAGE_PU=0.92`
- Increase reconnect delay: `FIDVR_MOTOR_RECONNECT_DELAY_S=15.0`

---

## Next Steps After Validation

1. **Scale up:** Once Cases A–C are convincing, increase `FEEDER_COUNT` to 2–3
2. **Extend duration:** Try `SIM_TARGET_TIME=60.0` to see long-term stability
3. **Multiple scenarios:** Test different fault durations, locations, load scales
4. **Paper plots:** Generate publication-ready figures with Cases A–C
5. **Control studies:** Disable/enable caps and regs separately to quantify their impact

---

## Reference: Original vs. Redesigned Parameters

| Parameter | Original | Redesigned | Why |
|-----------|----------|-----------|-----|
| Initiator | Line trip | Fault | More realistic sag depth/speed |
| `SIM_FINE_DT` | 0.03s | 0.005s | Capture fault transients |
| `SIM_COARSE_DT` | 0.03s | 0.02s | Finer resolution post-fault |
| `FEEDER_COUNT` | 2 | 1 | Easier validation |
| Motor Reconnect | 6s | 10s | Longer delayed recovery |
| Motor Q Scale | 5.0 | 6.5 | Stronger reactive surge |
| Motor Stall V | 0.72 | 0.71 | Captures deeper sag |

