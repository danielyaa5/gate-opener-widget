"""An interactive demo must never load real configuration or use a controller."""
import base64
import io
import json
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from unittest.mock import patch

from gate_bridge.auth import make_signature
from scripts.demo_bridge import DEMO_SECRET, make_server

try:
    from PIL import Image
except ImportError:
    Image = None


@unittest.skipIf(Image is None, "requires optional camera dependencies")
class DemoBridgeTests(unittest.TestCase):
    def test_demo_provides_private_camera_and_simulated_open_without_real_configuration(self):
        with patch("scripts.demo_bridge.Config.from_env", side_effect=AssertionError("must not load real configuration")), \
             patch("gate_bridge.position.GatePositionService._load_snapshot", side_effect=AssertionError("must not use Ring")):
            server = make_server(0, pulse_delay=0, movement_seconds=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                origin = f"http://127.0.0.1:{server.server_port}"

                def request(path, method="GET"):
                    stamp, nonce = str(time.time()), uuid.uuid4().hex
                    headers = {"X-Gate-Timestamp": stamp, "X-Gate-Nonce": nonce,
                               "X-Gate-Signature": make_signature(DEMO_SECRET, stamp, nonce, method, path, b"")}
                    req = urllib.request.Request(origin + path, headers=headers, method=method)
                    with urllib.request.urlopen(req, timeout=3) as response:
                        return json.load(response)

                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(origin + "/v1/camera", timeout=3)
                self.assertEqual(error.exception.code, 401)
                self.assertEqual(request("/v1/position")["state"], "closed")
                first = request("/v1/camera")
                with Image.open(io.BytesIO(base64.b64decode(first["image_base64"]))) as image:
                    self.assertEqual(image.size, (960, 540))
                    image.verify()
                self.assertEqual(first["camera_name"], "Demo camera")
                self.assertEqual(request("/v1/open", "POST")["result"], "command_accepted")
                self.assertEqual(server.gate.pulses, 1)
                self.assertEqual(request("/v1/position")["state"], "open")
                self.assertNotEqual(first["image_base64"], request("/v1/camera")["image_base64"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
