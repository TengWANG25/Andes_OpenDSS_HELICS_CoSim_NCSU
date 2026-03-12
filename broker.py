#!/usr/bin/env python3
"""Start and hold a HELICS broker process for co-simulation."""

import time
import helics as h


def main() -> None:
    broker = h.helicsCreateBroker("zmq", "mainbroker", "--federates=11 --port=23406")
    if not h.helicsBrokerIsConnected(broker):
        raise RuntimeError("Failed to start HELICS broker")

    try:
        while h.helicsBrokerIsConnected(broker):
            time.sleep(0.2)
    finally:
        h.helicsBrokerDisconnect(broker)


if __name__ == "__main__":
    main()