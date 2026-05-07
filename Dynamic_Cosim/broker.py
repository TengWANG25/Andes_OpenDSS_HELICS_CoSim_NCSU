#!/usr/bin/env python3
"""Start and hold a HELICS broker process for co-simulation."""

import os
import time
import helics as h


def main() -> None:
    federates = int(os.environ.get("BROKER_FEDERATES", "11"))
    port = int(os.environ.get("BROKER_PORT", "23407"))
    init_string = f"--federates={federates} --port={port} --reuse_address"

    print(f"Broker: starting with federates={federates} port={port}")
    broker = h.helicsCreateBroker("zmq", "mainbroker", init_string)
    if not h.helicsBrokerIsConnected(broker):
        raise RuntimeError("Failed to start HELICS broker")

    try:
        while h.helicsBrokerIsConnected(broker):
            time.sleep(0.2)
    finally:
        h.helicsBrokerDisconnect(broker)


if __name__ == "__main__":
    main()
