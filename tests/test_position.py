from __future__ import annotations

import io
import asyncio
import base64
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import sys
import unittest
from unittest.mock import Mock, patch

from gate_bridge.position import CameraSnapshot, GatePositionService, classify_snapshot, movement_path, preview_path, write_private_json
from gate_bridge.camera_monitor import save_snapshot_line, watch_worker

try:
    from PIL import Image, ImageDraw, ImageEnhance
except ImportError:
    Image = None


@unittest.skipIf(Image is None, "optional camera dependencies are not installed")
class CameraPositionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 1000.0
        self.config = {"references": []}
        for state in ["open", "closed"]:
            for mode in ["day", "night"]:
                data = self.fixture(state, mode)
                path = self.root / f"{state}-{mode}.jpg"
                path.write_bytes(data)
                self.config["references"].append({"state": state, "lighting": mode, "path": str(path)})
        self.config_path = self.root / "config.json"
        self.config_path.write_text(json.dumps(self.config))

    def fixture(self, state: str, mode: str, brightness: float = 1) -> bytes:
        image = Image.new("RGB", (160, 160), (80, 120, 170) if mode == "day" else (95, 95, 95))
        draw = ImageDraw.Draw(image)
        if state == "closed":
            for x in range(15, 155, 20):
                draw.rectangle((x, 12, x + 9, 150), fill=(220, 220, 220))
            draw.rectangle((5, 85, 155, 95), fill=(35, 35, 35))
        else:
            draw.polygon([(116, 0), (159, 0), (159, 159), (133, 159)], fill=(220, 220, 220))
            draw.line((10, 135, 110, 35), fill=(25, 25, 25), width=8)
        output = io.BytesIO()
        ImageEnhance.Brightness(image).enhance(brightness).save(output, format="JPEG")
        return output.getvalue()

    def service(self, snapshot: CameraSnapshot) -> GatePositionService:
        return GatePositionService(self.config_path, snapshot_loader=Mock(return_value=snapshot), clock=lambda: self.now)

    def test_open_and_closed_are_recognized_in_each_lighting_mode(self) -> None:
        for state in ["open", "closed"]:
            for mode in ["day", "night"]:
                with self.subTest(state=state, mode=mode):
                    result = classify_snapshot(self.fixture(state, mode, brightness=0.8), self.config)
                    self.assertEqual(result[:2], (state, "matched"))

    def test_missing_day_examples_cannot_reuse_night_examples(self) -> None:
        self.config["references"] = [r for r in self.config["references"] if r["lighting"] == "night"]
        self.assertEqual(classify_snapshot(self.fixture("closed", "day"), self.config)[:2], ("unknown", "needs_day_references"))

    def test_obstructed_and_ambiguous_views_are_not_called_closed(self) -> None:
        blank = io.BytesIO()
        Image.new("RGB", (160, 160), "black").save(blank, format="JPEG")
        self.assertEqual(classify_snapshot(blank.getvalue(), self.config)[0], "unknown")
        left = Image.open(io.BytesIO(self.fixture("open", "night")))
        right = Image.open(io.BytesIO(self.fixture("closed", "night")))
        middle = io.BytesIO()
        Image.blend(left, right, 0.5).save(middle, format="JPEG")
        self.assertEqual(classify_snapshot(middle.getvalue(), self.config)[0], "unknown")

    def test_identical_open_and_closed_references_are_rejected(self) -> None:
        for r in self.config["references"]:
            if r["state"] == "closed":
                r["path"] = str(self.root / f'open-{r["lighting"]}.jpg')
        self.assertEqual(classify_snapshot(self.fixture("open", "night"), self.config)[0], "unknown")

    def test_old_or_future_images_are_never_reported_as_current(self) -> None:
        for captured in [950, 1100, float("nan")]:
            with self.subTest(captured=captured):
                result = self.service(CameraSnapshot(self.fixture("closed", "night"), captured)).observe()
                self.assertEqual((result["state"], result["reason"]), ("unknown", "stale_snapshot"))

    def test_camera_failure_does_not_reuse_an_earlier_closed_result(self) -> None:
        service = self.service(CameraSnapshot(self.fixture("closed", "night"), 999))
        self.assertEqual(service.observe()["state"], "closed")
        self.now += 11
        service.snapshot_loader.side_effect = OSError("offline")
        self.assertEqual(service.observe()["state"], "unknown")

    def test_concurrent_widgets_share_a_recent_read_only_observation(self) -> None:
        service = self.service(CameraSnapshot(self.fixture("closed", "night"), 999))
        self.assertEqual(service.observe()["state"], "closed")
        self.assertEqual(service.observe()["state"], "closed")
        service.snapshot_loader.assert_called_once()

    def test_pulse_invalidation_does_not_wait_for_a_slow_camera(self) -> None:
        entered, release = threading.Event(), threading.Event()
        def fetch(config):
            entered.set()
            release.wait(2)
            return CameraSnapshot(self.fixture("closed", "night"), 999)
        service = GatePositionService(self.config_path, snapshot_loader=fetch, clock=lambda: self.now)
        results = []
        thread = threading.Thread(target=lambda: results.append(service.observe()))
        thread.start()
        self.assertTrue(entered.wait(1))
        start = time.monotonic()
        service.invalidate()
        self.assertLess(time.monotonic() - start, 0.1)
        release.set()
        thread.join(2)
        self.assertEqual(results[0]["state"], "unknown")
        self.assertEqual(service.observe()["reason"], "movement")

    def test_unconfigured_camera_never_calls_the_snapshot_helper(self) -> None:
        helper = Mock()
        result = GatePositionService(None, snapshot_loader=helper).observe()
        self.assertEqual(result["reason"], "not_configured")
        helper.assert_not_called()

    def monitored(self):
        path = self.root / "observation.json"
        self.config["observation_file"] = str(path)
        self.config_path.write_text(json.dumps(self.config))
        service = self.service(CameraSnapshot(b"unused", 999))
        return path, service

    def observation(self, *, state="closed", captured=999, checked=1000):
        return {"state": state, "observed_at": captured, "checked_at": checked,
                "reason": "matched", "match_score": 0.99}

    def message(self, state="closed", captured=999):
        return json.dumps({"image": base64.b64encode(self.fixture(state, "night")).decode(),
                           "captured_at": captured}).encode() + b"\n"

    def test_monitored_observation_reads_without_requesting_another_snapshot(self) -> None:
        path, service = self.monitored()
        write_private_json(path, self.observation())
        self.assertEqual(service.observe()["state"], "closed")
        service.snapshot_loader.assert_not_called()

    def test_missing_stale_corrupt_and_future_monitored_data_is_unknown(self) -> None:
        path, service = self.monitored()
        self.assertEqual(service.observe()["state"], "unknown")
        for value in [self.observation(checked=900), self.observation(captured=900),
                      self.observation(checked=1100), self.observation(captured=1100),
                      self.observation(state="unexpected"), {"checked_at": float("nan")}, []]:
            path.write_text(json.dumps(value))
            self.assertEqual(service.observe()["state"], "unknown")
        path.write_text("broken json")
        self.assertEqual(service.observe()["state"], "unknown")
        service.snapshot_loader.assert_not_called()

    def test_command_invalidates_the_shared_cache_for_both_bridge_instances(self) -> None:
        path, first = self.monitored()
        second = self.service(CameraSnapshot(b"unused", 999))
        write_private_json(path, self.observation())
        self.assertEqual(second.observe()["state"], "closed")
        first.invalidate()
        self.assertEqual(second.observe()["reason"], "movement")
        self.now = 1021
        write_private_json(path, self.observation(checked=1001))
        self.assertEqual(second.observe()["reason"], "movement")
        write_private_json(path, self.observation(state="open", captured=1020, checked=1021))
        self.assertEqual(first.observe()["state"], "open")
        self.assertEqual(second.observe()["state"], "open")

    def test_corrupt_shared_movement_marker_cannot_reveal_a_closed_result(self) -> None:
        path, service = self.monitored()
        write_private_json(path, self.observation())
        movement_path(path).write_text("[]")
        self.assertEqual(service.observe()["state"], "unknown")

    def test_monitor_publishes_private_observations_and_clears_them_on_errors(self) -> None:
        path, service = self.monitored()
        self.assertEqual(save_snapshot_line(self.message(), self.config_path, now=1000), 10)
        self.assertEqual(service.observe()["state"], "closed")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        delay = save_snapshot_line(b'{"error":"camera_unavailable","retry_after":120}', self.config_path, now=1000)
        self.assertEqual(delay, 120)
        self.assertEqual(service.observe()["reason"], "camera_unavailable")
        self.assertNotIn("image", json.loads(path.read_text()))

    def test_monitor_does_not_publish_a_frame_from_before_a_command(self) -> None:
        path, service = self.monitored()
        service.invalidate()
        save_snapshot_line(self.message(captured=995), self.config_path, now=1021)
        self.assertEqual(json.loads(path.read_text())["state"], "unknown")
        save_snapshot_line(self.message("open", captured=1020), self.config_path, now=1021)
        self.assertEqual(json.loads(path.read_text())["state"], "open")

    def test_preview_keeps_one_private_picture_with_original_time_and_camera_identity(self) -> None:
        self.config.update(camera_id="123456789", camera_name="Gate camera")
        _, service = self.monitored()
        save_snapshot_line(self.message("closed", captured=999), self.config_path, now=1000)
        saved = preview_path(self.config_path, self.config)
        self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
        first = service.camera_preview()
        self.assertEqual(first["captured_at"], 999)
        self.assertEqual(first["ring_url"], "https://account.ring.com/account/dashboard?lv_d=123456789")
        self.assertEqual(base64.b64decode(first["image_base64"]), self.fixture("closed", "night"))
        save_snapshot_line(self.message("open", captured=1000), self.config_path, now=1001)
        self.assertEqual(base64.b64decode(service.camera_preview()["image_base64"]), self.fixture("open", "night"))
        self.assertEqual(len(list(self.root.glob("*.snapshot.json"))), 1)
        save_snapshot_line(b'{"error":"camera_unavailable"}', self.config_path, now=1002)
        self.assertEqual(service.camera_preview()["captured_at"], 1000)
        service.snapshot_loader.assert_not_called()

        self.config["camera_id"] = "987654321"
        self.config_path.write_text(json.dumps(self.config))
        self.assertIsNone(service.camera_preview()["image_base64"])

    def test_preview_rejects_corruption_future_times_and_old_cache_without_inventing_a_new_capture(self) -> None:
        _, service = self.monitored()
        path = preview_path(self.config_path, self.config)
        valid = {"camera_id": "", "captured_at": 999, "image_base64": base64.b64encode(self.fixture("open", "night")).decode()}
        self.assertIsNone(service.camera_preview()["image_base64"])
        for changes in [{"captured_at": 1100}, {"captured_at": -90000}, {"image_base64": "invalid!"}, {"image_base64": "YXNk"}]:
            write_private_json(path, {**valid, **changes})
            self.assertIsNone(service.camera_preview()["image_base64"])
        path.write_text("[]")
        self.assertIsNone(service.camera_preview()["image_base64"])
        write_private_json(path, valid)
        self.now = 1100
        self.assertEqual(service.camera_preview()["captured_at"], 999)
        service.snapshot_loader.assert_not_called()

    def test_preview_storage_failure_cannot_break_gate_observations(self) -> None:
        service = self.service(CameraSnapshot(self.fixture("closed", "night"), 999))
        with patch("gate_bridge.position.write_private_json", side_effect=OSError("disk full")):
            self.assertEqual(service.observe()["state"], "closed")

    def test_one_worker_process_streams_multiple_frames_and_is_cleaned_up(self) -> None:
        path, _ = self.monitored()
        pidfile = self.root / "worker.pid"
        script = ("import os,time,sys; from pathlib import Path; "
                  f"Path({str(pidfile)!r}).write_text(str(os.getpid())); "
                  f"sys.stdout.buffer.write({self.message('open',time.time())!r}+{self.message('closed',time.time())!r}); "
                  "sys.stdout.flush(); time.sleep(30)")
        asyncio.run(watch_worker(self.config_path, command=[sys.executable, "-u", "-c", script], max_messages=2))
        self.assertEqual(json.loads(path.read_text())["state"], "closed")
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pidfile.read_text()), 0)
