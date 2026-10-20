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
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from gate_bridge import server  # noqa: E402
from gate_bridge.config import Config  # noqa: E402
from scripts.setup_code import encode  # noqa: E402
from scripts.demo_camera import preview  # noqa: E402

DEMO_SECRET = "demo-secret-for-local-testing-only-0000"


class SimulatedGate:
    def __init__(self, config: Config) -> None:
        self.pulses = 0
        self.state = "closed"
        self.delay = 1.5

    def pulse(self) -> None:
        time.sleep(self.delay)
        self.pulses += 1
        self.state = "open" if self.state == "closed" else "closed"
        logging.info("Simulated gate pulse %d", self.pulses)

    def status(self) -> dict[str, object]:
        return {"ok": True, "simulated": True}

    def close(self) -> None:
        pass


class SimulatedCamera:
    def __init__(self, gate: SimulatedGate, movement_seconds: float = 3) -> None:
        self.gate = gate
        self.movement_seconds = movement_seconds
        self.moving_until = 0.0

    def invalidate(self) -> None:
        self.moving_until = time.time() + self.movement_seconds

    def observe(self) -> dict:
        now = time.time()
        moving = now < self.moving_until
        return {"state": "unknown" if moving else self.gate.state, "observed_at": now - 1,
                "checked_at": now, "reason": "movement" if moving else "matched", "match_score": None if moving else 1.0}

    def camera_preview(self) -> dict:
        return preview(self.gate.state, time.time() - 1)


def make_server(port: int = 8799, *, pulse_delay: float = 1.5, movement_seconds: float = 3) -> server.GateHTTPServer:
    # Explicit dummy configuration: never call Config.from_env or open .env.
    config = Config(device_id="demo", local_key="demo", address="127.0.0.1", protocol_version=3.3,
                    api_secret=DEMO_SECRET, host="127.0.0.1", port=port)
    with patch.object(server, "GateDevice", SimulatedGate):
        httpd = server.GateHTTPServer(config)
    httpd.gate.delay = pulse_delay
    httpd.position = SimulatedCamera(httpd.gate, movement_seconds)
    return httpd


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8799)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    httpd = make_server(args.port)
    print(f"Simulated bridge on http://127.0.0.1:{args.port}. Setup code:")
    print(encode(f"http://127.0.0.1:{args.port}", DEMO_SECRET), flush=True)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
