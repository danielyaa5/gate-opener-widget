from __future__ import annotations

import base64
import io
import json
from pathlib import Path
import tempfile
import unittest

try:
    from PIL import Image, ImageDraw
except ImportError:
    Image = None

from gate_bridge.learning import GateLearningService, CANDIDATE_LIFETIME, PROMPT_COOLDOWN
from gate_bridge.position import CameraSnapshot, classify_snapshot, save_preview, write_private_json, movement_path


@unittest.skipIf(Image is None, "optional camera dependencies are not installed")
class LearningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 10000.0
        self.path = self.root / "config.json"
        self.observation = self.root / "observation.json"
        self.images = {}
        refs = []
        for label in ["open", "closed"]:
            image = Image.new("RGB", (160, 160), (80, 120, 170))
            draw = ImageDraw.Draw(image)
            if label == "open":
                draw.polygon([(116, 0), (159, 0), (159, 159), (133, 159)], fill="white")
                draw.line((10, 135, 110, 35), fill="black", width=8)
            else:
                for x in range(15, 155, 20):
                    draw.rectangle((x, 12, x+9, 150), fill="white")
                draw.rectangle((5, 85, 155, 95), fill="black")
            out = io.BytesIO(); image.save(out, format="JPEG")
            self.images[label] = out.getvalue()
            p = self.root / (label + ".jpg"); p.write_bytes(out.getvalue())
            refs.append({"path": str(p), "state": label, "lighting": "day"})
        self.config = {"camera_id": "123", "camera_name": "Test camera", "references": refs,
                       "observation_file": str(self.observation)}
        write_private_json(self.path, self.config)
        self.service = GateLearningService(self.path, clock=lambda: self.now)
        self.publish()

    def publish(self, *, state="unknown", reason="unclear", captured=None, image=None):
        captured = self.now if captured is None else captured
        save_preview(CameraSnapshot(image or self.images["open"], captured), self.path, self.config, self.now)
        write_private_json(self.observation, {"state": state, "reason": reason, "observed_at": captured,
                                             "checked_at": self.now})

    def answer(self, candidate, state="open"):
        return self.service.answer({"candidate_id": candidate["id"], "state": state})

    def test_exact_picture_is_kept_even_when_new_preview_arrives(self):
        candidate = self.service.candidate()["candidate"]
        self.publish(image=self.images["closed"])
        self.assertEqual(self.service.candidate()["candidate"], candidate)
        self.assertEqual(self.answer(candidate), {"result": "saved"})
        updated = json.loads(self.path.read_text())
        learned = updated["references"][-1]
        self.assertEqual(Path(learned["path"]).read_bytes(), self.images["open"])
        self.assertEqual(classify_snapshot(self.images["closed"], updated)[0], "closed")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(Path(learned["path"]).stat().st_mode & 0o777, 0o600)
        self.assertTrue(self.path.with_suffix(".before-learning.json").exists())

    def test_answer_replay_is_idempotent_and_opposite_answer_is_rejected(self):
        candidate = self.service.candidate()["candidate"]
        self.answer(candidate)
        self.assertEqual(self.answer(candidate), {"result": "saved"})
        with self.assertRaises(ValueError): self.answer(candidate, "closed")
        self.assertEqual(len(json.loads(self.path.read_text())["references"]), 3)

    def test_conflicting_reference_is_rejected_without_changing_config(self):
        original = self.path.read_bytes()
        candidate = self.service.candidate()["candidate"]
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.answer(candidate, "closed")
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list((self.root / "references/learned").glob("*.jpg")), [])

    def test_skip_and_cooldown_do_not_add_a_reference_or_repeat_same_photo(self):
        candidate = self.service.candidate()["candidate"]
        self.answer(candidate, "skip")
        self.assertEqual(len(json.loads(self.path.read_text())["references"]), 2)
        self.assertIsNone(self.service.candidate()["candidate"])
        self.now += PROMPT_COOLDOWN + 1
        self.publish()
        self.assertIsNone(self.service.candidate()["candidate"])
        self.publish(image=self.images["closed"])
        self.assertIsNotNone(self.service.candidate()["candidate"])

    def test_no_prompt_for_failures_motion_known_state_or_old_images(self):
        for params in [{"reason":"camera_unavailable"}, {"reason":"movement"}, {"state":"closed","reason":"matched"},
                       {"captured":self.now-31}, {"captured":self.now+10}]:
            with self.subTest(params=params):
                self.publish(**params)
                self.assertIsNone(self.service.candidate()["candidate"])

    def test_recent_command_and_mismatched_preview_block_prompt(self):
        write_private_json(movement_path(self.observation), {"command_at":self.now-1,"moving_until":self.now+20})
        self.assertIsNone(self.service.candidate()["candidate"])
        movement_path(self.observation).unlink()
        saved = json.loads(self.observation.read_text()); saved["observed_at"] -= 1
        write_private_json(self.observation, saved)
        self.assertIsNone(self.service.candidate()["candidate"])

    def test_disable_is_shared_and_clears_pending(self):
        candidate = self.service.candidate()["candidate"]
        self.service.answer({"action":"set_enabled","enabled":False})
        other = GateLearningService(self.path, clock=lambda:self.now)
        self.assertEqual(other.candidate(), {"enabled":False,"candidate":None})
        with self.assertRaises(ValueError): self.answer(candidate)
        with self.assertRaises(ValueError): self.service.answer({"action":"set_enabled","enabled":"true"})

    def test_expired_candidate_and_changed_camera_cannot_be_labeled(self):
        candidate = self.service.candidate()["candidate"]
        self.now += CANDIDATE_LIFETIME + 1
        with self.assertRaises(ValueError): self.answer(candidate)
        self.now = 10000
        changed = {**self.config, "camera_id":"456"}
        write_private_json(self.path, changed)
        with self.assertRaises(ValueError): self.answer(candidate)

    def test_two_bridge_instances_share_one_question_and_answer(self):
        candidate = self.service.candidate()["candidate"]
        other = GateLearningService(self.path, clock=lambda:self.now)
        self.assertEqual(other.candidate()["candidate"]["id"], candidate["id"])
        self.answer(candidate)
        self.assertEqual(other.answer({"candidate_id":candidate["id"],"state":"open"}), {"result":"saved"})

    def test_missing_camera_is_disabled(self):
        self.assertEqual(GateLearningService(None).candidate(), {"enabled":False,"candidate":None})

    def test_only_exact_candidate_id_and_allowed_answers_are_accepted(self):
        candidate = self.service.candidate()["candidate"]
        for payload in [{"candidate_id":"wrong","state":"open"}, {"candidate_id":candidate["id"],"state":"moving"}, []]:
            with self.assertRaises(ValueError): self.service.answer(payload)


if __name__ == "__main__": unittest.main()
