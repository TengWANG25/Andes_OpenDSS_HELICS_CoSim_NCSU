#!/usr/bin/env bash
set -euo pipefail # Strict error handling

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "Python interpreter not found: $PYTHON. Activate your environment or set PYTHON=/path/to/python." >&2
  exit 1
fi
if ! command -v ss >/dev/null 2>&1; then
  echo "The ss command is required. Install iproute2 in your Linux environment." >&2
  exit 1
fi

PORT=23406 # Broker port

cleanup() {
  echo "Killing processes..."
  [[ -n "${BROKER_PID:-}" ]] && kill "$BROKER_PID" 2>/dev/null || true
  [[ -n "${TRANS_PID:-}"  ]] && kill "$TRANS_PID"  2>/dev/null || true
  [[ -n "${FEED_PIDS:-}"  ]] && kill $FEED_PIDS   2>/dev/null || true
}  # Cleanup function to kill background processes
trap cleanup SIGINT SIGTERM EXIT # Trap signals for cleanup

# Start broker
"$PYTHON" -u broker.py > broker.log 2>&1 & # Start HELICS broker
BROKER_PID=$!

# Wait for broker port
for i in {1..50}; do
  ss -lnt | grep -q ":$PORT" && break
  sleep 0.1
done
ss -lnt | grep -q ":$PORT" || { echo "Broker not listening on $PORT"; exit 1; } # Verify broker is listening

# Start transmission
"$PYTHON" -u Transmission.py > transmission.log 2>&1 & # Start transmission simulator
TRANS_PID=$! # Store transmission PID

# Start feeders
FEED_PIDS=""
for i in $(seq 1 10); do
  "$PYTHON" -u Distribution.py $i > feeder_${i}.log 2>&1 &
  FEED_PIDS="$FEED_PIDS $!"
done # Start distribution feeders

echo "Simulation running..." # Indicate simulation is running
wait # Wait for all background processes to finish
