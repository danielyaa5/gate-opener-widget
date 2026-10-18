from __future__ import annotations

import json
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from unittest.mock import patch

from gate_bridge.auth import make_signature
from gate_bridge.config import Config
from gate_bridge.device import GateCommandError
from gate_bridge.server import GateHTTPServer, parse_client_events


class FakeGateDevice:
    def __init__(self, config: Config) -> None:
        self.pulses = 0
        self.keepalive_started = False

    def start_keepalive(self) -> None:
        self.keepalive_started = True

    def close(self) -> None:
        pass

    def pulse(self) -> None:
        self.pulses += 1

    def status(self) -> dict[str, object]:
        return {"ok": True}


class GateServerTests(unittest.TestCase):
    secret = "s" * 32
    alexa_secret = "a" * 32

    def setUp(self) -> None:
        config = Config(
            device_id="device",
            local_key="key",
            address="127.0.0.1",
            protocol_version=3.3,
            api_secret=self.secret,
            host="127.0.0.1",
            port=0,
            alexa_api_secret=self.alexa_secret,
        )
        gate_patch = patch("gate_bridge.server.GateDevice", FakeGateDevice)
        gate_patch.start()
        self.addCleanup(gate_patch.stop)
        self.server = GateHTTPServer(config)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def test_does_not_start_controller_keepalive(self) -> None:
        self.assertFalse(self.server.gate.keepalive_started)

    def request(
        self,
        path: str,
        body: bytes,
        *,
        valid_signature: bool = True,
        client: str | None = None,
        secret: str | None = None,
    ):
        timestamp = str(time.time())
        nonce = uuid.uuid4().hex
        signature = make_signature(
            (secret or self.secret) if valid_signature else "x" * 32,
            timestamp,
            nonce,
            "POST",
            path,
            body,
        )
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.server.server_port}{path}",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Gate-Timestamp": timestamp,
                "X-Gate-Nonce": nonce,
                "X-Gate-Signature": signature,
                **({"X-Gate-Client": client} if client else {}),
            },
        )
        return urllib.request.urlopen(request, timeout=2)

    def open_body(self, key: str = "0123456789abcdef") -> bytes:
        return json.dumps({"idempotency_key": key}).encode()

    def test_open_reports_accepted_command_without_claiming_movement(self) -> None:
        with self.request("/v1/open", b"") as response:
            self.assertEqual(
                json.load(response),
                {
                    "success": True,
                    "result": "command_accepted",
                    "movement_verified": False,
                },
            )

        self.assertEqual(self.server.gate.pulses, 1)

    def test_alexa_client_uses_its_own_secret(self) -> None:
        with self.request(
            "/v1/open", self.open_body(), client="alexa", secret=self.alexa_secret
        ) as response:
            self.assertEqual(response.status, 200)

        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/v1/open", self.open_body("f" * 16), client="alexa")

        self.assertEqual(caught.exception.code, 401)
        self.assertEqual(self.server.gate.pulses, 1)

    def test_default_client_rejects_alexa_secret(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/v1/open", b"", secret=self.alexa_secret)

        self.assertEqual(caught.exception.code, 401)
        self.assertEqual(self.server.gate.pulses, 0)

    def test_rejects_unknown_client(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/v1/open", b"", client="other")

        self.assertEqual(caught.exception.code, 401)
        self.assertEqual(self.server.gate.pulses, 0)

    def test_repeated_idempotency_key_pulses_once(self) -> None:
        with self.request("/v1/open", self.open_body()) as response:
            self.assertEqual(response.status, 200)

        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/v1/open", self.open_body())

        self.assertEqual(caught.exception.code, 409)
        self.assertTrue(json.load(caught.exception)["duplicate"])
        self.assertEqual(self.server.gate.pulses, 1)

    def test_failed_open_still_consumes_idempotency_key(self) -> None:
        def fail() -> None:
            raise GateCommandError("lost response")

        self.server.gate.pulse = fail
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/v1/open", self.open_body())
        self.assertEqual(caught.exception.code, 502)

        del self.server.gate.pulse
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/v1/open", self.open_body())

        self.assertEqual(caught.exception.code, 409)
        self.assertEqual(self.server.gate.pulses, 0)

    def test_rejects_malformed_idempotency_key(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/v1/open", self.open_body("short"))

        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.server.gate.pulses, 0)

    def diagnostic_body(self) -> bytes:
        return json.dumps(
            {
                "events": [
                    {
                        "id": "event-1",
                        "timestamp": time.time(),
                        "component": "com.danielyakobian.GateOpener.widget",
                        "operation": "open",
                        "outcome": "failed",
                        "requestID": "abc123",
                        "errorDomain": "NSURLErrorDomain",
                        "errorCode": -1200,
                        "httpStatus": None,
                    }
                ]
            }
        ).encode()

    def test_accepts_authenticated_client_log_without_pulsing_gate(self) -> None:
        with self.request("/v1/client-log", self.diagnostic_body()) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(json.load(response), {"stored": 1})

        self.assertEqual(self.server.gate.pulses, 0)

    def test_rejects_unauthenticated_client_log(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(
                "/v1/client-log",
                self.diagnostic_body(),
                valid_signature=False,
            )

        self.assertEqual(caught.exception.code, 401)
        self.assertEqual(self.server.gate.pulses, 0)


class ClientDiagnosticTests(unittest.TestCase):
    def event(self, **overrides: object) -> dict[str, object]:
        event: dict[str, object] = {
            "id": "event-1",
            "timestamp": 1_754_000_000.0,
            "component": "com.danielyakobian.GateOpener.widget",
            "operation": "open",
            "outcome": "failed",
            "requestID": "abc123",
            "errorDomain": "NSURLErrorDomain",
            "errorCode": -1009,
            "httpStatus": None,
        }
        event.update(overrides)
        return event

    def parse(self, events: list[dict[str, object]]) -> list[dict[str, object]]:
        return parse_client_events(json.dumps({"events": events}).encode())

    def test_accepts_and_normalizes_safe_fields(self) -> None:
        event = self.event(secret="must-not-be-retained")

        parsed = self.parse([event])

        self.assertEqual(parsed[0]["request_id"], "abc123")
        self.assertNotIn("secret", parsed[0])

    def test_rejects_unknown_outcome(self) -> None:
        with self.assertRaisesRegex(ValueError, "outcome"):
            self.parse([self.event(outcome="contains-arbitrary-text")])

    def test_accepts_retrying_outcome(self) -> None:
        parsed = self.parse([self.event(outcome="retrying", errorCode=-1200)])

        self.assertEqual(parsed[0]["outcome"], "retrying")
        self.assertEqual(parsed[0]["error_code"], -1200)

    def test_rejects_oversized_batch(self) -> None:
        with self.assertRaisesRegex(ValueError, "event count"):
            self.parse([self.event() for _ in range(51)])


if __name__ == "__main__":
    unittest.main()
