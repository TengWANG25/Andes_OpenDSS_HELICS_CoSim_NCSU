#!/usr/bin/env bash
set -euo pipefail # Strict error handling

PORT="${PORT:-23406}" # Broker port
FIDVR_ENABLE="${FIDVR_ENABLE:-0}"
FIDVR_MOTOR_MODEL="${FIDVR_MOTOR_MODEL:-surrogate}"
FIDVR_PROFILE="${FIDVR_PROFILE:-scaled}"
case "$FIDVR_PROFILE" in
  scaled|weak_bus14|alerts)
    ;;
  *)
    echo "Invalid FIDVR_PROFILE='$FIDVR_PROFILE'. Expected 'scaled', 'alerts', or 'weak_bus14'." >&2
    exit 2
    ;;
esac

TARGET_TIME_WAS_SET=0
[[ -n "${TARGET_TIME+x}" ]] && TARGET_TIME_WAS_SET=1
FEEDER_COUNT_WAS_SET=0
[[ -n "${FEEDER_COUNT+x}" ]] && FEEDER_COUNT_WAS_SET=1
TX_INTERFACE_BUS_WAS_SET=0
[[ -n "${TX_INTERFACE_BUS+x}" ]] && TX_INTERFACE_BUS_WAS_SET=1
DIST_VOLTAGE_BUS_WAS_SET=0
[[ -n "${DIST_VOLTAGE_BUS+x}" ]] && DIST_VOLTAGE_BUS_WAS_SET=1
DIST_LOAD_SCALE_WAS_SET=0
[[ -n "${DIST_LOAD_SCALE+x}" ]] && DIST_LOAD_SCALE_WAS_SET=1
FIDVR_MOTOR_SHARE_WAS_SET=0
[[ -n "${FIDVR_MOTOR_SHARE+x}" ]] && FIDVR_MOTOR_SHARE_WAS_SET=1
COARSE_DT_WAS_SET=0
[[ -n "${COARSE_DT+x}" ]] && COARSE_DT_WAS_SET=1
FIDVR_ENABLE_REG_CONTROL_WAS_SET=0
[[ -n "${FIDVR_ENABLE_REG_CONTROL+x}" ]] && FIDVR_ENABLE_REG_CONTROL_WAS_SET=1
FIDVR_ENABLE_CAP_CONTROL_WAS_SET=0
[[ -n "${FIDVR_ENABLE_CAP_CONTROL+x}" ]] && FIDVR_ENABLE_CAP_CONTROL_WAS_SET=1
FIDVR_CAPACITOR_INITIAL_FRACTION_WAS_SET=0
[[ -n "${FIDVR_CAPACITOR_INITIAL_FRACTION+x}" ]] && FIDVR_CAPACITOR_INITIAL_FRACTION_WAS_SET=1
FIDVR_ALERT_SIGNAL_WAS_SET=0
[[ -n "${FIDVR_ALERT_SIGNAL+x}" ]] && FIDVR_ALERT_SIGNAL_WAS_SET=1
FIDVR_ALERT_BUS_WAS_SET=0
[[ -n "${FIDVR_ALERT_BUS+x}" ]] && FIDVR_ALERT_BUS_WAS_SET=1
TX_DISTURBANCE_LINES_WAS_SET=0
if [[ -n "${TX_POSTFAULT_LINES+x}" || -n "${TX_POSTFAULT_LINE+x}" || -n "${TX_DISTURBANCE_LINES+x}" || -n "${TX_DISTURBANCE_LINE+x}" ]]; then
  TX_DISTURBANCE_LINES_WAS_SET=1
fi

if [[ -z "${TX_ENABLE_DISTURBANCE:-}" ]]; then
  if [[ "$FIDVR_ENABLE" == "1" ]]; then
    TX_ENABLE_DISTURBANCE="1"
  else
    TX_ENABLE_DISTURBANCE="0"
  fi
else
  TX_ENABLE_DISTURBANCE="$TX_ENABLE_DISTURBANCE"
fi
if [[ -z "${TX_KEEP_BUILTIN_EVENTS:-}" ]]; then
  TX_KEEP_BUILTIN_EVENTS="0"
else
  TX_KEEP_BUILTIN_EVENTS="$TX_KEEP_BUILTIN_EVENTS"
fi
# Fault-based disturbance defaults (no post-fault line outage by default)
if [[ -z "${TX_POSTFAULT_LINES:-}" ]]; then
  TX_POSTFAULT_LINES="${TX_DISTURBANCE_LINES:-}"
fi
if [[ -z "${TX_POSTFAULT_LINE:-}" ]]; then
  TX_POSTFAULT_LINE="${TX_DISTURBANCE_LINE:-}"
fi
if [[ -z "${TX_POSTFAULT_TRIP_DELAY:-}" ]]; then
  TX_POSTFAULT_TRIP_DELAY="0.01"
fi
# Fault timing: primary initiating event
if [[ -z "${TX_FAULT_TIME:-}" ]]; then
  TX_FAULT_TIME="1.0"
else
  TX_FAULT_TIME="$TX_FAULT_TIME"
fi
if [[ -z "${TX_FAULT_DURATION:-}" ]]; then
  TX_FAULT_DURATION="0.08"
else
  TX_FAULT_DURATION="$TX_FAULT_DURATION"
fi
TX_DISTURBANCE_TIME="$TX_FAULT_TIME"
TX_DISTURBANCE_DURATION="$TX_FAULT_DURATION"
TX_FAULT_RF="${TX_FAULT_RF:-0.0}"
# The IEEE14 dynamic case needs a numerically softer bus fault than the
# textbook near-zero reactance starting point. This default keeps the
# ANDES Fault path stable while still producing a strong initiating sag.
TX_FAULT_XF="${TX_FAULT_XF:-0.3}"
if [[ -z "${TARGET_TIME:-}" ]]; then
  if [[ "$FIDVR_ENABLE" == "1" ]]; then
    TARGET_TIME="20.0"
  elif [[ "$TX_ENABLE_DISTURBANCE" == "1" ]]; then
    TARGET_TIME="4.0"
  else
    TARGET_TIME="2.0"
  fi
else
  TARGET_TIME="$TARGET_TIME"
fi
FEEDER_COUNT="${FEEDER_COUNT:-1}"
PROGRESS_INTERVAL="${PROGRESS_INTERVAL:-5}"
FINE_DT="${FINE_DT:-0.005}"      # seconds; finer resolution for FIDVR fault event
COARSE_DT="${COARSE_DT:-0.02}"   # seconds; relaxed after fault recovery onset
if [[ -z "${COARSE_START:-}" ]]; then
  COARSE_START=$(awk -v t="$TX_FAULT_TIME" -v d="$TX_FAULT_DURATION" 'BEGIN { printf "%.3f", t + d + 0.5 }')
else
  COARSE_START="$COARSE_START"
fi
TX_CASE_XLSX="${TX_CASE_XLSX:-ieee14_fault.xlsx}"
if [[ -z "${TX_INTERFACE_BUS:-}" ]]; then
  if [[ "$FIDVR_ENABLE" == "1" ]]; then
    TX_INTERFACE_BUS="4"
  else
    TX_INTERFACE_BUS="2"
  fi
else
  TX_INTERFACE_BUS="$TX_INTERFACE_BUS"
fi
TX_VOLTAGE_TOPIC="${TX_VOLTAGE_TOPIC:-TxInterfaceVoltage}"
DIST_POWER_TOPIC_PREFIX="${DIST_POWER_TOPIC_PREFIX:-Feeder}"
DIST_MASTER_DSS="${DIST_MASTER_DSS:-13Bus/IEEE13Nodeckt.dss}"
DIST_VOLTAGE_BUS="${DIST_VOLTAGE_BUS:-650}"
COSIM_BASE_MVA="${COSIM_BASE_MVA:-100.0}"
if [[ -z "${DIST_LOAD_SCALE:-}" ]]; then
  if [[ "$FIDVR_ENABLE" == "1" ]]; then
    DIST_LOAD_SCALE="8.0"
  else
    DIST_LOAD_SCALE="1.0"
  fi
else
  DIST_LOAD_SCALE="$DIST_LOAD_SCALE"
fi
if [[ -z "${FIDVR_MOTOR_SHARE:-}" ]]; then
  if [[ "$FIDVR_MOTOR_MODEL" == "surrogate" ]]; then
    FIDVR_MOTOR_SHARE="0.45"
  else
    FIDVR_MOTOR_SHARE="0.35"
  fi
else
  FIDVR_MOTOR_SHARE="$FIDVR_MOTOR_SHARE"
fi
FIDVR_ENABLE_REG_CONTROL="${FIDVR_ENABLE_REG_CONTROL:-0}"
FIDVR_ENABLE_CAP_CONTROL="${FIDVR_ENABLE_CAP_CONTROL:-0}"
FIDVR_CAPACITOR_INITIAL_FRACTION="${FIDVR_CAPACITOR_INITIAL_FRACTION:-1.0}"
FIDVR_ALERT_SIGNAL="${FIDVR_ALERT_SIGNAL:-dist_bus}"
FIDVR_ALERT_BUS="${FIDVR_ALERT_BUS:-}"
FIDVR_REGULATOR_MONITOR_BUS="${FIDVR_REGULATOR_MONITOR_BUS:-}"
if [[ "$FIDVR_ENABLE" == "1" && "$FIDVR_PROFILE" == "scaled" ]]; then
  if [[ "$FEEDER_COUNT_WAS_SET" == "0" ]]; then
    # Start with one feeder for validation, scale up later
    FEEDER_COUNT="1"
  fi
  if [[ "$DIST_VOLTAGE_BUS_WAS_SET" == "0" ]]; then
    # Monitor a deeper feeder bus to see delayed recovery more clearly
    DIST_VOLTAGE_BUS="632"
  fi
  if [[ "$FIDVR_MOTOR_SHARE_WAS_SET" == "0" ]]; then
    FIDVR_MOTOR_SHARE="0.45"
  fi
  if [[ "$TX_DISTURBANCE_LINES_WAS_SET" == "0" ]]; then
    TX_POSTFAULT_LINES=""
    TX_POSTFAULT_LINE=""
  fi
fi
if [[ "$FIDVR_ENABLE" == "1" && "$FIDVR_PROFILE" == "alerts" ]]; then
  if [[ "$TARGET_TIME_WAS_SET" == "0" ]]; then
    TARGET_TIME="18.0"
  fi
  if [[ "$FEEDER_COUNT_WAS_SET" == "0" ]]; then
    FEEDER_COUNT="1"
  fi
  if [[ "$DIST_VOLTAGE_BUS_WAS_SET" == "0" ]]; then
    DIST_VOLTAGE_BUS="675"
  fi
  if [[ "$FIDVR_ALERT_SIGNAL_WAS_SET" == "0" ]]; then
    FIDVR_ALERT_SIGNAL="dist_bus"
  fi
  if [[ "$FIDVR_ALERT_BUS_WAS_SET" == "0" ]]; then
    FIDVR_ALERT_BUS=""
  fi
  if [[ "$DIST_LOAD_SCALE_WAS_SET" == "0" ]]; then
    DIST_LOAD_SCALE="8.0"
  fi
  if [[ "$FIDVR_MOTOR_SHARE_WAS_SET" == "0" ]]; then
    FIDVR_MOTOR_SHARE="0.65"
  fi
  if [[ "$FIDVR_ENABLE_CAP_CONTROL_WAS_SET" == "0" ]]; then
    FIDVR_ENABLE_CAP_CONTROL="1"
  fi
  if [[ "$FIDVR_ENABLE_REG_CONTROL_WAS_SET" == "0" ]]; then
    FIDVR_ENABLE_REG_CONTROL="1"
  fi
  if [[ "$FIDVR_CAPACITOR_INITIAL_FRACTION_WAS_SET" == "0" ]]; then
    FIDVR_CAPACITOR_INITIAL_FRACTION="1.0"
  fi
  TX_FAULT_DURATION="${TX_FAULT_DURATION:-0.10}"
  TX_FAULT_XF="${TX_FAULT_XF:-0.25}"
  FIDVR_CAPACITOR_ON_DELAY_S="${FIDVR_CAPACITOR_ON_DELAY_S:-4.0}"
  FIDVR_CAPACITOR_OFF_DELAY_S="${FIDVR_CAPACITOR_OFF_DELAY_S:-2.0}"
  FIDVR_CAPACITOR_ON_VOLTAGE_PU="${FIDVR_CAPACITOR_ON_VOLTAGE_PU:-0.97}"
  FIDVR_CAPACITOR_OFF_VOLTAGE_PU="${FIDVR_CAPACITOR_OFF_VOLTAGE_PU:-1.07}"
  FIDVR_REGULATOR_LOW_VOLTAGE_PU="${FIDVR_REGULATOR_LOW_VOLTAGE_PU:-1.03}"
  FIDVR_REGULATOR_HIGH_VOLTAGE_PU="${FIDVR_REGULATOR_HIGH_VOLTAGE_PU:-1.10}"
  FIDVR_REGULATOR_MONITOR_BUS="${FIDVR_REGULATOR_MONITOR_BUS:-632}"
  FIDVR_REGULATOR_DELAY_S="${FIDVR_REGULATOR_DELAY_S:-2.0}"
  FIDVR_REGULATOR_TAP_DELAY_S="${FIDVR_REGULATOR_TAP_DELAY_S:-0.5}"
  FIDVR_MOTOR_THERMAL_TRIP_TIME_S="${FIDVR_MOTOR_THERMAL_TRIP_TIME_S:-6.5}"
  FIDVR_MOTOR_THERMAL_TRIP_SPREAD_S="${FIDVR_MOTOR_THERMAL_TRIP_SPREAD_S:-0.5}"
  FIDVR_MOTOR_RECONNECT_DELAY_S="${FIDVR_MOTOR_RECONNECT_DELAY_S:-20.0}"
  FIDVR_MOTOR_RECONNECT_RAMP_S="${FIDVR_MOTOR_RECONNECT_RAMP_S:-6.0}"
  if [[ "$COARSE_DT_WAS_SET" == "0" ]]; then
    COARSE_DT="0.03"
  fi
  if [[ "$TX_DISTURBANCE_LINES_WAS_SET" == "0" ]]; then
    TX_POSTFAULT_LINES=""
    TX_POSTFAULT_LINE=""
  fi
fi
if [[ "$FIDVR_ENABLE" == "1" && "$FIDVR_PROFILE" == "weak_bus14" ]]; then
  if [[ "$TARGET_TIME_WAS_SET" == "0" ]]; then
    TARGET_TIME="10.0"
  fi
  if [[ "$FEEDER_COUNT_WAS_SET" == "0" ]]; then
    FEEDER_COUNT="1"
  fi
  if [[ "$TX_INTERFACE_BUS_WAS_SET" == "0" ]]; then
    TX_INTERFACE_BUS="14"
  fi
  if [[ "$DIST_VOLTAGE_BUS_WAS_SET" == "0" ]]; then
    DIST_VOLTAGE_BUS="675"
  fi
  if [[ "$DIST_LOAD_SCALE_WAS_SET" == "0" ]]; then
    DIST_LOAD_SCALE="1.0"
  fi
  if [[ "$TX_DISTURBANCE_LINES_WAS_SET" == "0" ]]; then
    TX_POSTFAULT_LINES=""
    TX_POSTFAULT_LINE=""
  fi
fi
BROKER_FEDERATES=$((FEEDER_COUNT + 1))
FIDVR_MOTOR_GROUP_TRIP_OFFSETS="${FIDVR_MOTOR_GROUP_TRIP_OFFSETS:-0,0,0}"
FIDVR_MOTOR_GROUP_RESTORE_OFFSETS="${FIDVR_MOTOR_GROUP_RESTORE_OFFSETS:-0,0,0}"
if [[ -z "${FIDVR_TRIGGER_TIME:-}" ]]; then
  if [[ "$FIDVR_ENABLE" == "1" ]]; then
    FIDVR_TRIGGER_TIME="$TX_FAULT_TIME"
  else
    FIDVR_TRIGGER_TIME="1.0"
  fi
else
  FIDVR_TRIGGER_TIME="$FIDVR_TRIGGER_TIME"
fi
if [[ -z "${FIDVR_FAULT_DURATION:-}" ]]; then
  if [[ "$FIDVR_ENABLE" == "1" ]]; then
    FIDVR_FAULT_DURATION="$TX_FAULT_DURATION"
  else
    FIDVR_FAULT_DURATION="0.10"
  fi
else
  FIDVR_FAULT_DURATION="$FIDVR_FAULT_DURATION"
fi
HELICS_BROKER_URL="${HELICS_BROKER_URL:-tcp://127.0.0.1:${PORT}}"

PYTHON=/home/teng/miniforge3/envs/cosim/bin/python

cleanup() {
  [[ -n "${MONITOR_PID:-}" ]] && kill "$MONITOR_PID" 2>/dev/null || true
  [[ -n "${BROKER_PID:-}" ]] && kill "$BROKER_PID" 2>/dev/null || true
  [[ -n "${TRANS_PID:-}"  ]] && kill "$TRANS_PID"  2>/dev/null || true
  for pid in "${FEED_PIDS[@]:-}"; do
    [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
  done
}  # Cleanup function to kill background processes
trap 'cleanup; exit 130' SIGINT
trap 'cleanup; exit 143' SIGTERM
trap cleanup EXIT # Trap signals for cleanup

BROKER_PID=""
TRANS_PID=""
MONITOR_PID=""
FEED_PIDS=()
START_EPOCH=$(date +%s)

format_duration() {
  local total="$1"
  local hours=$((total / 3600))
  local minutes=$(((total % 3600) / 60))
  local seconds=$((total % 60))
  printf "%02d:%02d:%02d" "$hours" "$minutes" "$seconds"
}

pid_is_alive() {
  local pid="${1:-}"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

extract_tx_time() {
  local file="${1:-transmission.log}"
  [[ -f "$file" ]] || { printf "n/a"; return; }
  awk '
    /\[TX progress\]/ {
      if (match($0, /t=([0-9.+-eE]+)s\//)) {
        val = substr($0, RSTART + 2, RLENGTH - 4)
      }
    }
    /Simulation terminated at t=/ {
      if (match($0, /t=([0-9.+-eE]+) s\./)) {
        val = substr($0, RSTART + 2, RLENGTH - 5)
      }
    }
    END {
      if (val != "") {
        printf "%.3f", val + 0.0
      } else {
        printf "n/a"
      }
    }
  ' "$file"
}

extract_feeder_time() {
  local file="$1"
  [[ -f "$file" ]] || { printf "n/a"; return; }
  awk '
    /t_granted=/ {
      if (match($0, /t_granted=([0-9.+-eE]+)s/)) {
        val = substr($0, RSTART + 10, RLENGTH - 11)
      }
    }
    END {
      if (val != "") {
        printf "%.3f", val + 0.0
      } else {
        printf "n/a"
      }
    }
  ' "$file"
}

format_progress() {
  local label="$1"
  local value="$2"
  if [[ "$value" == "n/a" ]]; then
    printf "%s=pending" "$label"
    return
  fi

  local pct
  pct=$(awk -v t="$value" -v target="$TARGET_TIME" 'BEGIN {
    if (target > 0) printf "%.1f", (100.0 * t / target);
    else printf "0.0";
  }')
  printf "%s=%ss/%ss (%s%%)" "$label" "$value" "$TARGET_TIME" "$pct"
}

count_alive_feeders() {
  local alive=0
  local pid
  for pid in "${FEED_PIDS[@]:-}"; do
    if pid_is_alive "$pid"; then
      alive=$((alive + 1))
    fi
  done
  printf "%d" "$alive"
}

print_progress_once() {
  local now elapsed tx_status tx_time feeder_time feeder_alive

  now=$(date +%s)
  elapsed=$(format_duration $((now - START_EPOCH)))

  if pid_is_alive "$TRANS_PID"; then
    tx_status="alive"
  else
    tx_status="done"
  fi

  tx_time=$(extract_tx_time "transmission.log")
  feeder_time=$(extract_feeder_time "feeder_1.log")
  feeder_alive=$(count_alive_feeders)

  printf '[progress %s] elapsed=%s tx=%s %s feeder1=%s feeders_alive=%s/%s\n' \
    "$(date '+%H:%M:%S')" \
    "$elapsed" \
    "$tx_status" \
    "$(format_progress "sim" "$tx_time")" \
    "$(format_progress "sim" "$feeder_time")" \
    "$feeder_alive" \
    "$FEEDER_COUNT"
}

progress_monitor() {
  while true; do
    sleep "$PROGRESS_INTERVAL"
    print_progress_once

    if ! pid_is_alive "$TRANS_PID" && [[ "$(count_alive_feeders)" == "0" ]]; then
      break
    fi
  done
}

# Start broker
BROKER_FEDERATES="$BROKER_FEDERATES" BROKER_PORT="$PORT" \
  "$PYTHON" -u broker.py > broker.log 2>&1 &
BROKER_PID=$!

# Wait for broker port
for i in {1..50}; do
  ss -lnt | grep -q ":$PORT" && break
  sleep 0.1
done
ss -lnt | grep -q ":$PORT" || { echo "Broker not listening on $PORT"; exit 1; } # Verify broker is listening

# Start transmission
SIM_TARGET_TIME="$TARGET_TIME" \
SIM_FINE_DT="$FINE_DT" \
SIM_COARSE_DT="$COARSE_DT" \
SIM_COARSE_START="$COARSE_START" \
HELICS_BROKER_URL="$HELICS_BROKER_URL" \
TX_CASE_XLSX="$TX_CASE_XLSX" \
TX_INTERFACE_BUS="$TX_INTERFACE_BUS" \
TX_VOLTAGE_TOPIC="$TX_VOLTAGE_TOPIC" \
DIST_POWER_TOPIC_PREFIX="$DIST_POWER_TOPIC_PREFIX" \
COSIM_BASE_MVA="$COSIM_BASE_MVA" \
FEEDER_COUNT="$FEEDER_COUNT" \
TX_ENABLE_DISTURBANCE="$TX_ENABLE_DISTURBANCE" \
TX_KEEP_BUILTIN_EVENTS="$TX_KEEP_BUILTIN_EVENTS" \
TX_FAULT_BUS="${TX_FAULT_BUS:-$TX_INTERFACE_BUS}" \
TX_FAULT_TIME="$TX_FAULT_TIME" \
TX_FAULT_DURATION="$TX_FAULT_DURATION" \
TX_FAULT_RF="$TX_FAULT_RF" \
TX_FAULT_XF="$TX_FAULT_XF" \
TX_POSTFAULT_LINES="$TX_POSTFAULT_LINES" \
TX_POSTFAULT_LINE="$TX_POSTFAULT_LINE" \
TX_POSTFAULT_TRIP_DELAY="$TX_POSTFAULT_TRIP_DELAY" \
  "$PYTHON" -u Transmission.py > transmission.log 2>&1 & # Start transmission simulator
TRANS_PID=$! # Store transmission PID

# Start feeders
for i in $(seq 1 "$FEEDER_COUNT"); do
  SIM_TARGET_TIME="$TARGET_TIME" \
    SIM_FINE_DT="$FINE_DT" \
    SIM_COARSE_DT="$COARSE_DT" \
    SIM_COARSE_START="$COARSE_START" \
    HELICS_BROKER_URL="$HELICS_BROKER_URL" \
    TX_VOLTAGE_TOPIC="$TX_VOLTAGE_TOPIC" \
    DIST_POWER_TOPIC_PREFIX="$DIST_POWER_TOPIC_PREFIX" \
    DIST_MASTER_DSS="$DIST_MASTER_DSS" \
    DIST_VOLTAGE_BUS="$DIST_VOLTAGE_BUS" \
    COSIM_BASE_MVA="$COSIM_BASE_MVA" \
    DIST_LOAD_SCALE="$DIST_LOAD_SCALE" \
    FIDVR_ENABLE="$FIDVR_ENABLE" \
    FIDVR_MOTOR_MODEL="$FIDVR_MOTOR_MODEL" \
    FIDVR_MOTOR_SHARE="$FIDVR_MOTOR_SHARE" \
    FIDVR_MOTOR_GROUP_TRIP_OFFSETS="$FIDVR_MOTOR_GROUP_TRIP_OFFSETS" \
    FIDVR_MOTOR_GROUP_RESTORE_OFFSETS="$FIDVR_MOTOR_GROUP_RESTORE_OFFSETS" \
    FIDVR_ENABLE_REG_CONTROL="$FIDVR_ENABLE_REG_CONTROL" \
    FIDVR_ENABLE_CAP_CONTROL="$FIDVR_ENABLE_CAP_CONTROL" \
    FIDVR_CAPACITOR_INITIAL_FRACTION="$FIDVR_CAPACITOR_INITIAL_FRACTION" \
    FIDVR_ALERT_SIGNAL="$FIDVR_ALERT_SIGNAL" \
    FIDVR_ALERT_BUS="$FIDVR_ALERT_BUS" \
    FIDVR_REGULATOR_MONITOR_BUS="$FIDVR_REGULATOR_MONITOR_BUS" \
    FIDVR_TRIGGER_TIME="$FIDVR_TRIGGER_TIME" \
    FIDVR_FAULT_DURATION="$FIDVR_FAULT_DURATION" \
    "$PYTHON" -u Distribution.py $i > feeder_${i}.log 2>&1 &
  FEED_PIDS+=("$!")
done # Start distribution feeders

echo "Simulation running..."
echo "  broker pid: $BROKER_PID"
echo "  transmission pid: $TRANS_PID"
echo "  feeder count: $FEEDER_COUNT"
echo "  FIDVR profile: $FIDVR_PROFILE"
echo "  target simulation time: ${TARGET_TIME}s"
if [[ "$FINE_DT" == "$COARSE_DT" ]]; then
  echo "  co-simulation step schedule: constant ${FINE_DT}s"
else
  echo "  co-simulation step schedule: ${FINE_DT}s until ${COARSE_START}s, then ${COARSE_DT}s"
fi
echo "  transmission case: $TX_CASE_XLSX"
echo "  transmission interface bus: $TX_INTERFACE_BUS"
echo "  distribution case: $DIST_MASTER_DSS"
echo "  distribution monitor bus: $DIST_VOLTAGE_BUS"
echo "  distribution alert signal: $FIDVR_ALERT_SIGNAL"
if [[ -n "$FIDVR_ALERT_BUS" ]]; then
  echo "  distribution alert bus: $FIDVR_ALERT_BUS"
fi
echo "  distribution load scale: $DIST_LOAD_SCALE"
echo "  FIDVR motor model: $FIDVR_MOTOR_MODEL"
echo "  FIDVR motor share: $FIDVR_MOTOR_SHARE"
echo "  FIDVR motor trip offsets: $FIDVR_MOTOR_GROUP_TRIP_OFFSETS"
echo "  FIDVR motor restore offsets: $FIDVR_MOTOR_GROUP_RESTORE_OFFSETS"
echo "  FIDVR regulator control: $FIDVR_ENABLE_REG_CONTROL"
if [[ -n "$FIDVR_REGULATOR_MONITOR_BUS" ]]; then
  echo "  FIDVR regulator monitor bus: $FIDVR_REGULATOR_MONITOR_BUS"
fi
echo "  FIDVR capacitor control: $FIDVR_ENABLE_CAP_CONTROL"
echo "  FIDVR capacitor initial fraction: $FIDVR_CAPACITOR_INITIAL_FRACTION"
echo "  FIDVR trigger time: $FIDVR_TRIGGER_TIME"
echo "  FIDVR fault duration: $FIDVR_FAULT_DURATION"
echo "  co-simulation base MVA: $COSIM_BASE_MVA"
echo "  disturbance enabled: $TX_ENABLE_DISTURBANCE"
echo "  keep workbook events: $TX_KEEP_BUILTIN_EVENTS"
echo "  FIDVR enabled: $FIDVR_ENABLE"
if [[ "$TX_ENABLE_DISTURBANCE" == "1" ]]; then
  echo "  fault bus: ${TX_FAULT_BUS:-$TX_INTERFACE_BUS}"
  echo "  fault time: $TX_FAULT_TIME"
  echo "  fault duration: $TX_FAULT_DURATION"
  echo "  fault rf/xf: $TX_FAULT_RF / $TX_FAULT_XF"
  echo "  post-fault lines: ${TX_POSTFAULT_LINES:-${TX_POSTFAULT_LINE:-none}}"
fi
echo "  expected federates: $BROKER_FEDERATES"
echo "  logs:"
echo "    broker.log"
echo "    transmission.log"
echo "    feeder_*.log"
echo "  progress heartbeat: every ${PROGRESS_INTERVAL}s"

progress_monitor &
MONITOR_PID=$!

SIM_EXIT=0

if ! wait "$TRANS_PID"; then
  SIM_EXIT=1
  echo "Transmission process exited with an error. Stopping feeders and broker."
  for pid in "${FEED_PIDS[@]}"; do
    [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
  done
  if pid_is_alive "$BROKER_PID"; then
    kill "$BROKER_PID" 2>/dev/null || true
  fi
fi

for pid in "${FEED_PIDS[@]}"; do
  if ! wait "$pid"; then
    SIM_EXIT=1
  fi
done

if pid_is_alive "$BROKER_PID"; then
  kill "$BROKER_PID" 2>/dev/null || true
fi
wait "$BROKER_PID" 2>/dev/null || true

if [[ -n "${MONITOR_PID:-}" ]]; then
  kill "$MONITOR_PID" 2>/dev/null || true
  wait "$MONITOR_PID" 2>/dev/null || true
fi

print_progress_once

if [[ "$SIM_EXIT" == "0" ]]; then
  echo "Simulation finished."
else
  echo "Simulation finished with one or more process errors."
fi

trap - SIGINT SIGTERM EXIT
exit "$SIM_EXIT"
