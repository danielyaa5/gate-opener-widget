"""Exercise the real Swift client against an isolated HTTP bridge, never hardware."""
from __future__ import annotations

import json
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("swiftc"), "requires macOS and Swift")
class NativeClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(prefix="gate-native-test-")
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(__file__).resolve().parents[1]
        source = Path(cls.temp.name) / "Probe.swift"
        cls.binary = Path(cls.temp.name) / "probe"
        source.write_text('''import Foundation
@main struct Probe {
    static func main() async {
        let client = GateAPIClient(baseURL: URL(string: CommandLine.arguments[1])!, secret: String(repeating: "s", count: 32))
        do {
            if CommandLine.arguments[2] == "camera" {
                let preview = try await client.cameraPreview()
                print("\\(preview.cameraName)|\\(preview.ringURL != nil)|\\(preview.imageData() != nil)")
                return
            }
            if CommandLine.arguments[2] == "position" {
                let observation = try await client.gatePosition()
                print(observation.currentState())
                return
            }
            if CommandLine.arguments[2] == "status" {
                _ = try await client.isOnline()
            } else {
                try await client.openGate()
            }
            print("accepted")
        } catch {
            print("failed: \\(error.localizedDescription)")
        }
    }
}
''')
        shared = root / "ios/GateOpener/Shared"
        subprocess.run([
            "swiftc", "-parse-as-library", str(shared / "GateSettings.swift"),
            str(shared / "GateAPIClient.swift"), str(shared / "GatePosition.swift"), str(source), "-o", str(cls.binary),
        ], check=True, capture_output=True, timeout=60)

    def run_client(self, payload: object, *, status: int = 200, operation: str = "open") -> tuple[str, int]:
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self):
                is_diagnostic = self.path == "/v1/client-log"
                if self.path == "/v1/open":
                    calls.append(self.path)
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                body = json.dumps({"stored": 25} if is_diagnostic else payload).encode()
                self.send_response(200 if is_diagnostic else status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = reply
            do_POST = reply

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = subprocess.run([
                str(self.binary), f"http://127.0.0.1:{server.server_port}", operation,
            ], check=True, capture_output=True, text=True, timeout=15)
            return result.stdout.strip(), len(calls)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_accepts_one_confirmed_pulse(self) -> None:
        self.assertEqual(self.run_client({"success": True, "result": "command_accepted"}), ("accepted", 1))

    def test_camera_preview_preserves_capture_age_and_only_opens_ring_links(self) -> None:
        payload = {"camera_name": "Gate camera", "ring_url": "https://account.ring.com/account/dashboard?lv_d=123456789",
                   "captured_at": time.time(), "image_base64": base64.b64encode(b"\xff\xd8example").decode()}
        self.assertEqual(self.run_client(payload, operation="camera"), ("Gate camera|true|true", 0))
        for changed in [{"captured_at": time.time() - 90000}, {"captured_at": time.time() + 60},
                        {"image_base64": "invalid"}]:
            self.assertEqual(self.run_client({**payload, **changed}, operation="camera"), ("Gate camera|true|false", 0))
        self.assertEqual(self.run_client({**payload, "ring_url": "https://example.com"}, operation="camera"),
                         ("Gate camera|false|true", 0))

    def test_rejects_unconfirmed_success_without_retrying(self) -> None:
        for payload in ({"success": False}, {}, {"success": True, "result": "unknown"}, "not JSON status"):
            with self.subTest(payload=payload):
                output, calls = self.run_client(payload)
                self.assertIn("failed:", output)
                self.assertEqual(calls, 1)

    def test_shows_bridge_error_without_retrying(self) -> None:
        output, calls = self.run_client({"error": "Gate activation was not confirmed"}, status=502)
        self.assertIn("502", output)
        self.assertIn("not confirmed", output)
        self.assertEqual(calls, 1)

    def test_online_requires_a_valid_positive_status(self) -> None:
        for payload, expected in (({"online": True}, "accepted"), ({"online": False}, "failed:"), ({}, "failed:")):
            with self.subTest(payload=payload):
                output, calls = self.run_client(payload, operation="status")
                self.assertIn(expected, output)
                self.assertEqual(calls, 0)

    def test_position_observations_are_read_only_and_expire(self) -> None:
        now = time.time()
        for state, observed_at, expected in [("open", now, "open"), ("closed", now, "closed"), ("closed", now - 600, "unknown"), ("closed", now + 60, "unknown"), ("unexpected", now, "unknown")]:
            with self.subTest(state=state, observed_at=observed_at):
                payload = {"state": state, "observed_at": observed_at, "checked_at": now, "reason": "matched"}
                output, calls = self.run_client(payload, operation="position")
                self.assertEqual((output, calls), (expected, 0))
