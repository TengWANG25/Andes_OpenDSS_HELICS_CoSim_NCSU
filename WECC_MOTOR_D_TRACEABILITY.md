# WECC Motor D Traceability

Reference document:

`WECC Air Conditioner Motor Model Test Report-- Final.pdf`


## Map

| Code location | Implemented item | Report page(s) |
| --- | --- | --- |
| `Distribution.py::get_fidvr_config` | Motor D parameter defaults: `CompPF`, `Vstall`, `Tstall`, `Frst`, `Vrst`, `Trst`, `Vbrk`, contactor voltages, thermal parameters, and UV relay parameters | pp. 66, 75 |
| `Distribution.py::get_fidvr_config` | Current `Rstall=0.114`, `Xstall=0.124` defaults | p. 58 |
| `Distribution.py::_wecc_motor_baseline_kvar` | Baseline motor kvar from compressor power factor, `Q = P * tan(acos(PF))` | pp. 58, 66 |
| `Distribution.py::build_wecc_motor_d_motors` | Splitting a fraction of ordinary constant power load into an A/C motor component, | pp. 66-67 |
| `Distribution.py::_wecc_motor_d_running_pq_pu` | Running state voltagendependent `P/Q` curves using `Kp1/Np1/Kq1/Nq1`, `Kp2/Np2/Kq2/Nq2`, `CompPF`, and `Vbrk` | pp. 66, 68 |
| `Distribution.py::_wecc_motor_d_stall_pq_pu` | Stalled Motor D as constant impedance using `Rstall`, `Xstall`, and terminal voltage | pp. 68-69, 70-73 |
| `Distribution.py::_wecc_motor_d_vstall_break` | Numerical transition helper between running and stalled curves | Derived from pp. 68-69 |
| `Distribution.py::_update_wecc_motor_d_state` | Stall arming: voltage below `Vstall` for at least `Tstall` | pp. 18, 21, 69 |
| `Distribution.py::_wecc_contactor_fraction` | Contactor dropout/reclose thresholds `Vc1off`, `Vc2off`, `Vc1on`, `Vc2on` | pp. 25-27, 66, 75 |
| `Distribution.py::_wecc_thermal_fraction` and `_wecc_update_temperature` | Thermal overload heating/tripping using `Tth`, `Th1t`, `Th2t` | pp. 5-7, 11, 14, 66, 75 |
| `Distribution.py::_update_wecc_motor_d_state` restart branch | Restartable motor fraction `Frst`, restart voltage `Vrst`, restart delay `Trst` | pp. 29, 31, 33-34, 66, 75 |
| `Distribution.py::_update_wecc_motor_d_state` UV relay branch | UV relay fraction and pickup timers: `Fuvr`, `UVtr1/Ttr1`, `UVtr2/Ttr2` | pp. 36, 38, 40-41, 66, 75 |
| `fidvr_load_restoration.py` and `Distribution.py::_update_wecc_thermal_load_restoration` | Optional long delay post thermal load restoration extension | The WECC report notes thermal protection may take several minutes to re-close on p. 6, but strict LD1PAC says thermally tripped load does not reconnect on pp. 6 and 68. This extension is therefore marked optional and is not part of the strict WECC Motor D core. |

## Parameter Notes

Most default values come directly from the Appendix 1 model description and
Appendix 3 composite load settings tables:

```text
CompPF=0.97
Vstall=0.70
Tstall=0.033
Vbrk=0.86
Frst=0.20
Vrst=0.90
Trst=0.40
Vc1off=0.45
Vc2off=0.35
Vc1on=0.50
Vc2on=0.40
Tth=10.0
Th1t=1.30
Th2t=4.30
Fuvr=0.00
UVtr1=0.80
Ttr1=0.20
UVtr2=0.90
Ttr2=5.00
```

The report is internally inconsistent for `Rstall`/`Xstall`:

- p. 58, Section 9 states `rSTALL=0.114` and `xSTALL=0.124`.
- pp. 66 and 75 list `Rstall=0.124` and `Xstall=0.114`.

The current code follows p. 58 because that section explicitly discusses the
stalling parameter validation cases. 

## Implementation Caveats

- `Distribution.py` implements Motor D as controlled OpenDSS `Load.weccmd_*`
  terminal injections.
- The running and stalled `P/Q` equations are evaluated in the feeder federate,
  then written to OpenDSS as equivalent terminal load injections.
- The contactor model uses the WECC voltage thresholds with a hysteretic
  fraction update. The report also discusses contactor operating time on p. 27;
  this implementation does not add a separate 1-2 cycle contactor delay.
- The long delay thermal restoration path is separate from the
  strict WECC Motor D core and is off by default.
