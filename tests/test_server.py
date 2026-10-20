from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from unittest.mock import patch

from gate_bridge.auth import make_signature
from gate_bridge.auto_close import AutoCloser, AutoClosePolicy, send_conditional_close
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

    def position_request(self, *, authorized: bool = True, alexa: bool = False, path: str = "/v1/position"):
        timestamp, nonce = str(time.time()), uuid.uuid4().hex
        secret = self.alexa_secret if alexa else self.secret
        headers = {"X-Gate-Timestamp": timestamp, "X-Gate-Nonce": nonce,
                   "X-Gate-Signature": make_signature(secret, timestamp, nonce, "GET", path, b"") if authorized else "invalid"}
        if alexa:
            headers["X-Gate-Client"] = "alexa"
        return urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{self.server.server_port}{path}", headers=headers), timeout=2)

    def test_position_read_never_contacts_the_gate_controller(self) -> None:
        with patch.object(self.server.gate, "status", side_effect=AssertionError("must not query controller")):
            with self.position_request() as response:
                self.assertEqual(json.load(response)["state"], "unknown")
        self.assertEqual(self.server.gate.pulses, 0)

    def test_camera_request_requires_authentication_before_observation(self) -> None:
        with patch.object(self.server.position, "observe") as observe:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.position_request(authorized=False)
            self.assertEqual(caught.exception.code, 401)
            observe.assert_not_called()

    def test_alexa_key_does_not_grant_camera_access(self) -> None:
        with patch.object(self.server.position, "observe") as observe:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.position_request(alexa=True)
            self.assertEqual(caught.exception.code, 403)
            observe.assert_not_called()

    def test_camera_preview_is_authenticated_private_and_read_only(self) -> None:
        payload = {"camera_name": "Gate camera", "captured_at": 1000, "image_base64": "private-image"}
        with patch.object(self.server.position, "camera_preview", return_value=payload) as preview:
            for authorized, alexa, status in [(False, False, 401), (True, True, 403)]:
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.position_request(path="/v1/camera", authorized=authorized, alexa=alexa)
                self.assertEqual(caught.exception.code, status)
                preview.assert_not_called()
            with patch.object(self.server.gate, "status", side_effect=AssertionError("must not query controller")):
                with self.position_request(path="/v1/camera") as response:
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    self.assertEqual(json.load(response), payload)
            preview.assert_called_once()
        self.assertEqual(self.server.gate.pulses, 0)

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
        with patch.object(self.server.position, "invalidate") as invalidate:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.request("/v1/open", self.open_body())
            invalidate.assert_called_once()
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

    def automatic_setup(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "rule.json"
        path.write_text(json.dumps({"enabled": True, "timezone": "America/Los_Angeles"}))
        self.server.auto_close_config = path
        now = datetime.fromisoformat("2026-10-20T22:00:00-07:00").timestamp()
        observation = {"state": "open", "reason": "matched", "observed_at": now,
                       "checked_at": now, "match_score":0.99, "open_since": now-2401}
        return path, now, observation

    def conditional_body(self, since, key="0123456789abcdef"):
        return json.dumps({"idempotency_key": key, "open_since": since}).encode()

    def test_conditional_close_rechecks_the_real_route_and_pulses_only_once(self):
        path, now, observation = self.automatic_setup()
        with patch("gate_bridge.server.time.time", return_value=now), patch.object(self.server.position, "observe", return_value=observation):
            body = self.conditional_body(observation["open_since"])
            with self.request("/v1/close-if-open", body) as response:
                self.assertTrue(json.load(response)["success"])
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.request("/v1/close-if-open", body)
            self.assertEqual(caught.exception.code, 409)
        self.assertEqual(self.server.gate.pulses, 1)

    def test_conditional_close_skips_closed_unclear_short_stale_and_changed_episodes(self):
        path, now, observation = self.automatic_setup()
        for changes in [{"state":"closed"}, {"state":"unknown"}, {"reason":"unclear"},
                        {"observed_at":now-31}, {"checked_at":now-31},
                        {"open_since":now-2000}, {"open_since":now-3000}]:
            current = observation | changes
            with patch("gate_bridge.server.time.time", return_value=now), patch.object(self.server.position, "observe", return_value=current):
                with self.request("/v1/close-if-open", self.conditional_body(observation["open_since"], uuid.uuid4().hex)) as response:
                    self.assertEqual(json.load(response)["result"], "skipped")
        self.assertEqual(self.server.gate.pulses, 0)

    def test_conditional_close_cannot_run_during_the_day_or_when_disabled(self):
        path, now, observation = self.automatic_setup()
        for enabled, date in [(True,"2026-10-20T15:00:00-07:00"),(False,"2026-10-20T22:00:00-07:00")]:
            clock = datetime.fromisoformat(date).timestamp()
            path.write_text(json.dumps({"enabled":enabled,"timezone":"America/Los_Angeles"}))
            current=observation | {"observed_at":clock,"checked_at":clock,"open_since":clock-2401}
            with patch("gate_bridge.server.time.time",return_value=clock),patch.object(self.server.position,"observe",return_value=current):
                with self.request("/v1/close-if-open",self.conditional_body(current["open_since"],uuid.uuid4().hex)) as response:
                    self.assertEqual(json.load(response)["result"],"skipped")
        self.assertEqual(self.server.gate.pulses,0)

    def test_conditional_close_rejects_unauthenticated_and_alexa_requests(self):
        path, now, observation = self.automatic_setup()
        with patch.object(self.server.position, "observe") as observe:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request("/v1/close-if-open",self.conditional_body(now),valid_signature=False)
            self.assertEqual(error.exception.code,401)
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request("/v1/close-if-open",self.conditional_body(now),client="alexa",secret=self.alexa_secret)
            self.assertEqual(error.exception.code,403)
            observe.assert_not_called()
        self.assertEqual(self.server.gate.pulses,0)

    def test_auto_closer_http_failure_stays_latched_across_a_restart(self):
        path, now, observation = self.automatic_setup()
        config=Config(device_id="fake",local_key="fake",address="127.0.0.1",protocol_version=3.3,
                      api_secret=self.secret,port=self.server.server_port)
        def lost_ack():
            self.server.gate.pulses+=1
            raise GateCommandError("response lost")
        with patch("gate_bridge.server.time.time",return_value=now),patch.object(self.server.position,"observe",return_value=observation),patch.object(self.server.gate,"pulse",side_effect=lost_ack):
            sender=lambda since,key:send_conditional_close(config,since,key)
            journal=path.with_suffix(".state.json")
            closer=AutoCloser(journal,sender,clock=lambda:now)
            self.assertEqual(closer.step(observation,AutoClosePolicy.load(path))["status"],"needs_attention")
            AutoCloser(journal,sender,clock=lambda:now).step(observation,AutoClosePolicy.load(path))
        self.assertEqual(self.server.gate.pulses,1)

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
