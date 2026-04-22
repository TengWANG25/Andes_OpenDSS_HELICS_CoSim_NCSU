#!/usr/bin/env bash
set -euo pipefail # Strict error handling

PORT=23406 # Broker port
TARGET_TIME=10.0 # seconds; shared by transmission and distribution simulators
FEEDER_COUNT=1
PROGRESS_INTERVAL=10
BROKER_FEDERATES=$((FEEDER_COUNT + 1))
FINE_DT=0.03      # seconds; resolve the event so the feeder sees the voltage sag
COARSE_DT=0.03    # seconds; switch to a larger outer step after transients settle
COARSE_START=2.00 # seconds; keep this after the local trip/reclose window

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
  "$PYTHON" -u Transmission.py > transmission.log 2>&1 & # Start transmission simulator
TRANS_PID=$! # Store transmission PID

# Start feeders
for i in $(seq 1 "$FEEDER_COUNT"); do
  SIM_TARGET_TIME="$TARGET_TIME" \
    SIM_FINE_DT="$FINE_DT" \
    SIM_COARSE_DT="$COARSE_DT" \
    SIM_COARSE_START="$COARSE_START" \
    "$PYTHON" -u Distribution.py $i > feeder_${i}.log 2>&1 &
  FEED_PIDS+=("$!")
done # Start distribution feeders

echo "Simulation running..."
echo "  broker pid: $BROKER_PID"
echo "  transmission pid: $TRANS_PID"
echo "  feeder count: $FEEDER_COUNT"
echo "  target simulation time: ${TARGET_TIME}s"
echo "  co-simulation step schedule: ${FINE_DT}s until ${COARSE_START}s, then ${COARSE_DT}s"
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
