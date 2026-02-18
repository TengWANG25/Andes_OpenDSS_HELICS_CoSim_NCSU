#!/usr/bin/env bash
set -euo pipefail # Strict error handling

PORT=23406 # Broker port

cleanup() {
  echo "Killing processes..."
  [[ -n "${BROKER_PID:-}" ]] && kill "$BROKER_PID" 2>/dev/null || true
  [[ -n "${TRANS_PID:-}"  ]] && kill "$TRANS_PID"  2>/dev/null || true
  [[ -n "${FEED_PIDS:-}"  ]] && kill $FEED_PIDS   2>/dev/null || true
}  # Cleanup function to kill background processes
trap cleanup SIGINT SIGTERM EXIT # Trap signals for cleanup

# Start broker
helics_broker -t zmq -f 11 --name=mainbroker --port=$PORT --loglevel=trace > broker.log 2>&1 & # Start HELICS broker
BROKER_PID=$!

# Wait for broker port
for i in {1..50}; do
  ss -lnt | grep -q ":$PORT" && break
  sleep 0.1
done
ss -lnt | grep -q ":$PORT" || { echo "Broker not listening on $PORT"; exit 1; } # Verify broker is listening

# Start transmission
python3 -u Transmission.py > transmission.log 2>&1 & # Start transmission simulator
TRANS_PID=$! # Store transmission PID

# Start feeders
FEED_PIDS=""
for i in $(seq 1 10); do
  python3 -u Distribution.py $i > feeder_${i}.log 2>&1 &
  FEED_PIDS="$FEED_PIDS $!"
done # Start distribution feeders

echo "Simulation running..." # Indicate simulation is running
wait # Wait for all background processes to finish
