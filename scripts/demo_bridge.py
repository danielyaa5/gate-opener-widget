#!/usr/bin/env python3
"""Run a bridge with a simulated gate, for trying the apps or recording demos.

Nothing talks to a real controller: Open / Close only waits a moment and logs a pulse.
The demo secret is public, so bind to localhost only.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from gate_bridge import server  # noqa: E402
from gate_bridge.config import Config  # noqa: E402
from scripts.setup_code import encode  # noqa: E402

DEMO_SECRET = "demo-secret-for-local-testing-only-0000"


class SimulatedGate:
    def __init__(self, config: Config) -> None:
        self.pulses = 0

    def pulse(self) -> None:
        time.sleep(1.5)
        self.pulses += 1
        logging.info("Simulated gate pulse %d", self.pulses)

    def status(self) -> dict[str, object]:
        return {"ok": True, "simulated": True}

    def close(self) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8799)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    config = Config(
        device_id="demo", local_key="demo", address="127.0.0.1", protocol_version=3.3,
        api_secret=DEMO_SECRET, host="127.0.0.1", port=args.port,
    )
    server.GateDevice = SimulatedGate
    httpd = server.GateHTTPServer(config)
    print(f"Simulated bridge on http://127.0.0.1:{args.port}. Setup code:")
    print(encode(f"http://127.0.0.1:{args.port}", DEMO_SECRET), flush=True)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
