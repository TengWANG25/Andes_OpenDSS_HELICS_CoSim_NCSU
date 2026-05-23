# Case Study Observation and Conclusion Drafts

## Slide 13/14: Motor Share Sensitivity

Observation:

- The same balanced three-phase fault is applied at transmission bus 14 from 1.000 s to 1.080 s.
- The fault-on voltage nadir is almost unchanged across motor-share cases, so the initial voltage dip is mainly determined by the transmission fault.
- After the fault clears, the motor share strongly affects voltage recovery. Higher Motor D share gives lower post-fault feeder voltage and slower recovery.
- At 100% motor share, the feeder voltage remains close to or below 0.90 pu after 10 s, which is consistent with delayed voltage recovery caused by stalled motor reactive demand.

Useful numbers:

| Motor share | Transmission bus 14 minimum | Distribution minimum | Distribution final at 10 s |
|---:|---:|---:|---:|
| 0% | 0.363 pu | 0.361 pu | 1.004 pu |
| 25% | 0.363 pu | 0.363 pu | 0.969 pu |
| 50% | 0.363 pu | 0.365 pu | 0.920 pu |
| 75% | 0.364 pu | 0.367 pu | 0.917 pu |
| 100% | 0.364 pu | 0.369 pu | 0.891 pu |

Conclusion:

The motor-share sensitivity study shows that the motor composition mainly changes the post-fault recovery trajectory rather than the fault-on voltage nadir. A larger Motor D fraction increases reactive demand after fault clearing and produces a stronger FIDVR response.

## Slide 15: IEEE 13-Node Voltage Response Under the Same Fault

Observation:

- The same bus 14 three-phase fault is applied, but the feeder node voltages do not recover uniformly.
- During the fault, all nodes experience a severe voltage depression around 0.36-0.37 pu.
- After clearing, the source-side node 650 recovers near 1.0 pu, while downstream and single-phase/lateral nodes remain much lower.
- The lowest final average voltages occur at nodes 652, 611, 684, 634, and 675, showing that feeder topology and phase-domain effects shape the local FIDVR severity.

Useful numbers:

| Node | Minimum average voltage | Final average voltage at 10 s |
|---:|---:|---:|
| 650 | 0.364 pu | 0.995 pu |
| 652 | 0.366 pu | 0.813 pu |
| 684 | 0.367 pu | 0.827 pu |
| 611 | 0.368 pu | 0.820 pu |
| 675 | 0.369 pu | 0.892 pu |
| 634 | 0.370 pu | 0.883 pu |

Conclusion:

The IEEE 13-node results show why a feeder-resolved distribution model is useful. A single equivalent load can show the aggregate response, but it cannot show which feeder nodes experience the most delayed recovery.

## Slide 16: Transmission Fault Location and Fault Type

Observation:

- All disturbances start at 1.000 s and clear or reclose at 1.080 s.
- Three-phase bus faults produce much larger interface voltage depressions than temporary line trips.
- Faults electrically close to the transmission-distribution interface produce the strongest voltage sag at bus 14.
- Temporary line trips cause comparatively mild voltage deviations in this IEEE 14-bus case.

Useful numbers:

| Scenario | Type | Location | Minimum bus 14 voltage |
|---|---|---|---:|
| Bus 14 fault | 3-phase bus fault | Interface bus | 0.364 pu |
| Bus 9 fault | 3-phase bus fault | Near interface | 0.451 pu |
| Bus 13 fault | 3-phase bus fault | Near interface | 0.498 pu |
| Bus 10 fault | 3-phase bus fault | Remote load bus | 0.510 pu |
| Bus 4 fault | 3-phase bus fault | Remote load bus | 0.577 pu |
| Bus 1 fault | 3-phase bus fault | Slack generator bus | 0.646 pu |
| Bus 8 fault | 3-phase bus fault | Generator bus | 0.675 pu |
| Line 13 trip | Line trip/reclose | 9-14 interface corridor | 0.967 pu |
| Line 16 trip | Line trip/reclose | 13-14 near interface | 1.002 pu |
| Line 3 trip | Line trip/reclose | 2-3 generator corridor | 1.002 pu |
| Line 20 trip | Line trip/reclose | 8-7 generator corridor | 0.960 pu |

Conclusion:

The fault-location sweep shows that FIDVR severity depends strongly on the disturbance location and type. Near-interface three-phase bus faults are the most severe for the coupled feeder, while short line trips in this test system are not sufficient to create a strong delayed-recovery event.

## Final Conclusion Slide

Suggested conclusion:

- An ANDES-HELICS-OpenDSS dynamic co-simulation framework was developed to study FIDVR with both transmission dynamics and feeder-level voltage resolution.
- The framework couples the positive-sequence IEEE 14-bus transmission system with the phase-domain IEEE 13-node feeder and a WECC Motor D load representation.
- Case studies show that motor share mainly controls the post-fault recovery behavior, while fault location controls the severity of the initial voltage depression at the interface bus.
- Feeder-level results reveal non-uniform node voltage recovery, which would be hidden by a single equivalent distribution load.
- The most severe FIDVR response occurs when a high motor-share feeder is exposed to a strong near-interface transmission fault.

Suggested limitations / future work:

- Current transmission bus faults are balanced three-phase faults; unbalanced transmission faults require an additional sequence-domain or phase-domain extension.
- Motor D, capacitor, and regulator parameters should be further calibrated against utility or benchmark FIDVR cases.
- Future work should extend the study to multiple feeders and larger transmission systems, and quantify recovery metrics such as voltage nadir, time below 0.90 pu, recovery time, and reactive power demand.

## 30 s Full-Scope Demonstration Case

Recommendation:

A 30 s case is a good slide-friendly window for showing the complete FIDVR
sequence, but it should be described as an accelerated restoration demonstration.
The physical post-thermal compressor restoration delay is normally on the order
of minutes, so compressing it into 30 s is useful for visualization but should
not be presented as the natural customer-load restoration timescale.

Suggested study intent:

- Use 0-10 s to show fault, motor stalling, delayed recovery, and thermal trip.
- Use roughly 10-20 s to show load removal and regulator/capacitor voltage support.
- Use roughly 15-30 s to show accelerated thermal load restoration and the
  resulting voltage response.

Suggested command for a 30 s plot with all three alerts:

```bash
cd FIDVR
RUN_OUTPUT_DIR=runs/fidvr_30s_tune_cap_2p5_restore_visible \
PORT=28360 \
TARGET_TIME=30 \
FIDVR_ENABLE=1 \
FIDVR_PROFILE=second_half_fast_controls \
FIDVR_MOTOR_SHARE=1.0 \
TX_FAULT_BUS=14 \
TX_FAULT_TIME=1.0 \
TX_FAULT_DURATION=0.08 \
TX_FAULT_RF=0.0 \
TX_FAULT_XF=0.3 \
FIDVR_ENABLE_THERMAL_LOAD_RESTORATION=1 \
FIDVR_THERMAL_RESTORE_MIN_DELAY_S=22 \
FIDVR_THERMAL_RESTORE_MAX_DELAY_S=26 \
FIDVR_THERMAL_RESTORE_RAMP_S=8 \
FIDVR_THERMAL_RESTORE_VOLTAGE_PU=0.95 \
FIDVR_THERMAL_RESTORE_FRACTION=0.5 \
FIDVR_CAPACITOR_INITIAL_FRACTION=0 \
FIDVR_CAPACITOR_KVAR_SCALE=2.5 \
FIDVR_CAPACITOR_ON_DELAY_S=5.0 \
FIDVR_CAPACITOR_OFF_VOLTAGE_PU=1.08 \
./run.sh
```

Plot it with:

```bash
python3 plot_distribution_from_logs.py \
  --log runs/fidvr_30s_tune_cap_2p5_restore_visible/feeder_1.log \
  --out runs/fidvr_30s_tune_cap_2p5_restore_visible/plots \
  --xlim 0 30
```

How to describe this case:

This case keeps the same transmission-side fault but compresses the
post-thermal restoration delay so the main FIDVR stages can be viewed in a
30 s window. It is intended for mechanism visualization: fault-induced motor
stalling, thermal motor trip, regulator/capacitor voltage support, and staged
load restoration. The Motor D physical structure and WECC Motor D parameters
are unchanged. The tuned parameters are on the disturbance/control side: the
same bus 14 fault, capacitors initially offline, delayed capacitor closing, a
moderate aggregate switched-capacitor support level, and later partial thermal
load restoration. This lets the voltage-support action produce a true
normalized Alert.3 overvoltage before restored load pulls the voltage back down.

Pilot result:

The tuned 30 s run triggered all three feeder alerts and reached the load
restoration stage by the end of the window. Alert.1 triggered during the bus 14
fault, Alert.2 triggered after the voltage stayed below `0.90 pu` for 5 s, and
Alert.3 triggered when the normalized feeder voltage stayed above `1.05 pu` for
more than 1 s. Existing capacitor size, faster regulator timing, and a slightly
longer fault were not sufficient to sustain Alert.3 in this feeder; the
successful setting used modest additional switched-capacitor support and
delayed partial restoration. It should be presented as an accelerated mechanism
case rather than a calibrated utility event.
