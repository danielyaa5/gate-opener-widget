from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from gate_bridge.auto_close import AutoCloser, AutoClosePolicy
from gate_bridge.camera_monitor import track_open_duration
from gate_bridge.command_lock import CommandLock
from gate_bridge.device import GateBusyError


class AutoCloseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "auto-close.state.json"
        self.now = datetime.fromisoformat("2026-10-20T22:00:00-07:00").timestamp()
        self.policy = AutoClosePolicy(enabled=True, timezone="America/Los_Angeles")
        self.sender = Mock(return_value={"success": True, "result": "command_accepted"})
        self.closer = AutoCloser(self.path, self.sender, clock=lambda: self.now)

    def observation(self, state="open", **changes):
        value = {"state": state, "reason": "matched", "observed_at": self.now,
                 "checked_at": self.now, "match_score":0.99, "open_since": self.now - 2401 if state == "open" else None}
        value.update(changes)
        return value

    def test_local_time_window_including_midnight_and_dst(self):
        for date, expected in [("2026-10-20T20:59:59-07:00", False),
                               ("2026-10-20T21:00:00-07:00", True),
                               ("2026-10-21T00:30:00-07:00", True),
                               ("2026-10-21T05:59:59-07:00", True),
                               ("2026-10-21T06:00:00-07:00", False),
                               ("2026-11-01T01:30:00-07:00", True),
                               ("2026-11-01T01:30:00-08:00", True)]:
            self.assertEqual(self.policy.night(datetime.fromisoformat(date).timestamp()), expected)

    def test_explicit_midnight_cutoff(self):
        policy = AutoClosePolicy(enabled=True, end_hour=0, timezone="America/Los_Angeles")
        self.assertTrue(policy.night(datetime.fromisoformat("2026-10-20T23:59:00-07:00").timestamp()))
        self.assertFalse(policy.night(datetime.fromisoformat("2026-10-21T00:01:00-07:00").timestamp()))

    def test_short_unknown_old_future_and_disabled_observations_never_send(self):
        for value in [self.observation("closed"), self.observation("unknown"),
                      self.observation(open_since=self.now-2399), self.observation(open_since=None),
                      self.observation(observed_at=self.now-31), self.observation(checked_at=self.now-31),
                      self.observation(match_score=0.8),
                      self.observation(observed_at=self.now+1), self.observation(open_since=float("nan"))]:
            self.closer.step(value, self.policy)
        self.closer.step(self.observation(), AutoClosePolicy())
        self.sender.assert_not_called()

    def test_one_attempt_is_durable_across_unknown_images_and_process_restarts(self):
        self.closer.step(self.observation(), self.policy)
        self.sender.assert_called_once()
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        restarted = AutoCloser(self.path, self.sender, clock=lambda: self.now)
        for state in ["unknown", "open", "unknown", "open"]:
            self.now += 10
            restarted.step(self.observation(state), self.policy)
        self.sender.assert_called_once()

    def test_lost_response_does_not_retry_and_claim_precedes_transmission(self):
        def lose_response(since, key):
            state = json.loads(self.path.read_text())
            self.assertTrue(state["attempted"])
            self.assertEqual(state["idempotency_key"], key)
            raise TimeoutError("response lost")
        sender = Mock(side_effect=lose_response)
        closer = AutoCloser(self.path, sender, clock=lambda: self.now)
        self.assertEqual(closer.step(self.observation(), self.policy)["status"], "needs_attention")
        AutoCloser(self.path, sender, clock=lambda: self.now).step(self.observation(), self.policy)
        sender.assert_called_once()

    def test_three_distinct_closed_frames_rearm_for_a_new_open_episode(self):
        self.closer.step(self.observation(), self.policy)
        self.now += 30
        closed = self.observation("closed")
        for _ in range(3): self.closer.step(closed, self.policy)
        self.assertTrue(self.closer.state["attempted"])
        for _ in range(2):
            self.now += 10
            self.closer.step(self.observation("closed"), self.policy)
        self.assertFalse(self.closer.state["attempted"])
        self.assertEqual(self.closer.state["status"], "closed_confirmed")
        since = self.now + 10
        self.now = since
        self.closer.step(self.observation(open_since=since), self.policy)
        self.sender.assert_called_once()
        self.now += 2401
        self.closer.step(self.observation(open_since=since), self.policy)
        self.assertEqual(self.sender.call_count, 2)

    def test_closed_verification_resets_after_an_unclear_frame(self):
        self.closer.step(self.observation(), self.policy)
        for state in ["closed", "closed", "unknown", "closed", "closed"]:
            self.now += 10
            self.closer.step(self.observation(state), self.policy)
        self.assertTrue(self.closer.state["attempted"])
        self.now += 10
        self.closer.step(self.observation("closed"), self.policy)
        self.assertFalse(self.closer.state["attempted"])

    def test_unknown_outcome_remains_latched_after_a_verification_timeout(self):
        self.closer.step(self.observation(), self.policy)
        self.now += 121
        self.assertEqual(self.closer.step(self.observation(), self.policy)["status"], "needs_attention")
        self.sender.assert_called_once()

    def test_explicit_skip_can_be_rechecked_without_a_hardware_attempt(self):
        self.sender.return_value = {"result": "skipped", "reason": "conditions_changed"}
        self.closer.step(self.observation(), self.policy)
        self.assertFalse(self.closer.state["attempted"])
        self.sender.return_value = {"success": True, "result": "command_accepted"}
        self.now += 10
        self.closer.step(self.observation(), self.policy)
        self.assertEqual(self.sender.call_count, 2)

    def test_corrupt_journal_requires_review_instead_of_resetting_attempts(self):
        for data in ["broken", "[]", '{"version":1}']:
            self.path.write_text(data)
            with self.assertRaises(ValueError): AutoCloser(self.path, self.sender)
        self.sender.assert_not_called()

    def test_continuous_open_duration_resets_on_uncertainty_gaps_or_commands(self):
        beginning = self.now
        previous = None
        for seconds in range(0, 2400, 10):
            self.now = beginning + seconds
            value = self.observation(open_since=None)
            track_open_duration(value, previous)
            self.closer.step(value, self.policy)
            previous = value
        self.sender.assert_not_called()
        self.now = beginning + 2400
        value = self.observation(open_since=None)
        track_open_duration(value, previous)
        self.closer.step(value, self.policy)
        self.sender.assert_called_once()
        for interrupted in [self.observation("unknown"), self.observation("closed"),
                            self.observation(observed_at=self.now-31)]:
            value = self.observation(open_since=None)
            track_open_duration(value, interrupted)
            self.assertEqual(value["open_since"], self.now)

    def test_two_bridge_instances_cannot_hold_the_command_lock_together(self):
        config = self.root / "camera.json"
        config.write_text(json.dumps({"observation_file": str(self.root/"observation.json")}))
        first, second = CommandLock(config), CommandLock(config)
        with first.acquire():
            with self.assertRaises(GateBusyError):
                with second.acquire(): self.fail("overlapping commands")
        with second.acquire(): pass
