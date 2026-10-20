from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from gate_bridge.auto_close import AutoCloser, AutoClosePolicy, send_conditional_close
from gate_bridge.away import AwayPolicy, AwayTripGuard, TagTrackerAwayService, SOURCE, displayed_distance, MILE_METERS
from gate_bridge.config import Config
from gate_bridge.server import GateHTTPServer


class TagTrackerAwayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'history.sqlite'
        self.now = datetime.fromisoformat('2026-10-20T12:00:00-07:00').timestamp()
        self.policy = AwayPolicy.parse({'enabled': True, 'history_db': str(self.db),
                                       'device_name': 'Example Phone', 'observer_at_home': True})
        self.service = TagTrackerAwayService(clock=lambda: self.now)
        with sqlite3.connect(self.db) as db:
            db.execute('CREATE TABLE observations(id INTEGER PRIMARY KEY,ts TEXT,kind TEXT,name TEXT,location TEXT,staleness TEXT,distance TEXT,raw_json TEXT)')

    def record(self, *, kind='devices', name='Example Phone', location='Example Park', age='Now', distance='2.0 mi', captured=None, extra=None, status_ok=True):
        captured = self.now - 10 if captured is None else captured
        raw = dict(source=SOURCE, name=name, location=location, staleness=age, distance=distance,
                   captured_at=captured, finished_at=self.now - 1, complete_scan=True, scan_id='example-scan',
                   confidence=1, distance_confidence=1, confirmed_captures=2, confirmation_started_at=captured-1)
        raw.update(extra or {})
        stamp = datetime.fromtimestamp(captured, timezone.utc).isoformat()
        with sqlite3.connect(self.db) as db:
            db.execute('INSERT INTO observations(ts,kind,name,location,staleness,distance,raw_json) VALUES (?,?,?,?,?,?,?)',
                       (stamp, kind, name, location, age, distance, json.dumps(raw)))
        (self.root / 'device-collection-status.json').write_text(json.dumps(
            dict(ok=status_ok, scan_id='example-scan', checked_at=self.now - 1)))

    def camera(self, **changes):
        return dict(state='open', reason='matched', observed_at=self.now, checked_at=self.now,
                    open_since=self.now - 30, match_score=.99) | changes

    def daytime_policy(self):
        return AutoClosePolicy(enabled=True, timezone='America/Los_Angeles', away=self.policy)

    def test_policy_requires_stationary_home_observer_and_exact_name(self):
        for values in ({'enabled': True}, {'enabled': True, 'history_db': str(self.db), 'device_name': 'Example Phone'},
                       {'enabled': True, 'history_db': str(self.db), 'device_name': 'Example Phone', 'observer_at_home': True, 'max_scan_age': 300}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                AwayPolicy.parse(values)

    def test_current_scan_clearly_outside_is_eligible(self):
        self.record()
        observation = self.service.observe(self.policy)
        self.assertEqual(observation['state'], 'away')
        self.assertTrue(self.policy.eligible(observation, self.now))

    def test_rounded_boundary_does_not_close(self):
        for distance in ('1 mi', '1.0 mi', '1.1 mi', '1,234 mi', 'not a distance'):
            with self.subTest(distance=distance):
                self.record(distance=distance)
                self.assertFalse(self.policy.eligible(self.service.observe(self.policy), self.now))
        self.assertGreater(displayed_distance('1.2 mi', 200)[1], MILE_METERS)
        self.assertGreater(displayed_distance('2.0 km', 200)[1], MILE_METERS)
        self.assertLess(displayed_distance('5000 ft', 200)[1], MILE_METERS)

    def test_nearby_is_preserved_as_near(self):
        self.record(distance='Nearby')
        self.assertEqual(self.service.observe(self.policy)['state'], 'near')

    def test_home_requires_home_label_current_age_and_two_agreeing_captures(self):
        for location, age, distance, extra in [('Home', 'Now', 'Nearby', {}),
                ('Home', 'Now', '0.0 mi', {}), ('Example Park', 'Now', 'Nearby', {}),
                ('Home', 'Now', '0.5 mi', {}), ('Home', '10 min ago', 'Nearby', {}),
                ('Home', 'Now', 'Nearby', {'confirmed_captures': 1})]:
            self.record(location=location, age=age, distance=distance, extra=extra)
            expected = location == 'Home' and age == 'Now' and distance in ('Nearby', '0.0 mi') and not extra
            self.assertEqual(self.policy.confirmed_home(self.service.observe(self.policy), self.now), expected)

    def test_recent_scan_of_old_or_unknown_findmy_location_is_not_fresh(self):
        for age in ('1 min. ago', '20 min ago', '', 'Age unavailable', 'Offline'):
            self.record(age=age)
            self.assertEqual(self.service.observe(self.policy)['state'], 'unknown')

    def test_stale_future_incomplete_and_low_confidence_scans_never_close(self):
        for extra in ({'source': 'other'}, {'complete_scan': False}, {'confidence': .8},
                      {'confirmed_captures': 1}, {'confirmation_started_at': self.now},
                      {'distance_confidence': .8}, {'distance': '8 mi'}, {'name': 'Other Phone'},
                      {'captured_at': self.now + 1}, {'finished_at': self.now + 1},
                      {'finished_at': self.now - 100}):
            self.record(extra=extra)
            self.assertFalse(self.policy.eligible(self.service.observe(self.policy), self.now))
        self.record(captured=self.now - 91)
        self.assertEqual(self.service.observe(self.policy)['state'], 'unknown')
        self.record(status_ok=False)
        self.assertEqual(self.service.observe(self.policy)['state'], 'unknown')

    def test_exact_device_identity_excludes_item_or_other_phone(self):
        self.record(kind='items')
        self.record(name='Example Phone Old')
        self.assertEqual(self.service.observe(self.policy)['reason'], 'device_not_seen')

    def test_latest_unavailable_reading_invalidates_old_away(self):
        self.record()
        self.record(location='No location found', age='', distance='')
        self.assertEqual(self.service.observe(self.policy)['state'], 'unknown')

    def test_broken_or_missing_history_is_unknown(self):
        self.db.unlink()
        self.assertEqual(self.service.observe(self.policy)['state'], 'unknown')
        self.assertFalse(self.db.exists())

    def test_daytime_away_and_night_rules_share_one_durable_attempt(self):
        self.record()
        send = Mock(return_value={'success': True, 'result': 'command_accepted'})
        path = self.root / 'journal.json'
        closer = AutoCloser(path, send, clock=lambda: self.now)
        result = closer.step(self.camera(), self.daytime_policy(), self.service.observe(self.policy))
        self.assertEqual(result['trigger'], 'device_away')
        self.assertEqual(result['status'], 'awaiting_closed')
        AutoCloser(path, send, clock=lambda: self.now).step(self.camera(open_since=self.now - 3000),
            replace(self.daytime_policy(), start_hour=0, end_hour=23), self.service.observe(self.policy))
        send.assert_called_once()

    def test_away_still_requires_continuously_open_fresh_camera(self):
        self.record()
        for changes in ({'state': 'closed'}, {'observed_at': self.now - 16}, {'open_since': self.now - 10}, {'match_score': .9}):
            self.assertFalse(self.daytime_policy().eligible(self.camera(**changes), self.now, self.service.observe(self.policy)))

    def test_one_close_per_trip_survives_closed_gate_restart_and_night_window(self):
        self.record()
        send = Mock(return_value={'success': True, 'result': 'command_accepted'})
        path = self.root / 'trip.state.json'
        closer = AutoCloser(path, send, clock=lambda: self.now)
        closer.step(self.camera(), self.daytime_policy(), self.service.observe(self.policy))
        self.assertTrue(json.loads(path.read_text())['away_latched'])
        for _ in range(3):
            self.now += 10
            closer.step(self.camera(state='closed', open_since=None), self.daytime_policy(), self.service.observe(self.policy))
        self.assertFalse(closer.state['attempted'])
        closer = AutoCloser(path, send, clock=lambda: self.now)
        self.now += 60
        self.record()
        night = replace(self.daytime_policy(), start_hour=0, end_hour=23)
        for location in [self.service.observe(self.policy), {'state': 'unknown'}]:
            self.assertEqual(closer.step(self.camera(open_since=self.now-3000), night, location)['status'], 'away_locked')
        self.record(distance='Nearby')  # Nearby outside the Home label does not rearm.
        closer.step(self.camera(state='closed', open_since=None), night, self.service.observe(self.policy))
        self.assertTrue(closer.state['away_latched'])
        send.assert_called_once()
        self.record(location='Home', distance='Nearby')
        closer.step(self.camera(state='closed', open_since=None), night, self.service.observe(self.policy))
        self.assertFalse(closer.state['away_latched'])
        self.now += 20
        self.record()
        AutoCloser(path, send, clock=lambda: self.now).step(self.camera(), self.daytime_policy(), self.service.observe(self.policy))
        self.assertEqual(send.call_count, 2)

    def test_skip_or_lost_response_still_uses_trip_until_new_home_report(self):
        for result in ({'result': 'skipped'}, TimeoutError('lost response')):
            path = self.root / ('skip.json' if isinstance(result, dict) else 'lost.json')
            def sender(since, key):
                self.assertTrue(json.loads(path.read_text())['away_latched'])
                if isinstance(result, Exception):raise result
                return result
            send = Mock(side_effect=sender)
            self.record()
            closer = AutoCloser(path, send, clock=lambda: self.now)
            closer.step(self.camera(), self.daytime_policy(), self.service.observe(self.policy))
            self.record(location='Home', distance='Nearby', captured=self.now-5)
            closer.step(self.camera(), self.daytime_policy(), self.service.observe(self.policy))
            self.assertTrue(closer.state['away_latched'])
            send.assert_called_once()

    def test_relay_trip_guard_survives_new_instance_and_only_home_rearms(self):
        path = self.root / 'relay-trip.json'
        self.record()
        guard = AwayTripGuard(path)
        self.assertTrue(guard.allow('device_away', self.policy, self.service.observe(self.policy), self.now, 'first'))
        self.assertFalse(AwayTripGuard(path).allow('device_away', self.policy, self.service.observe(self.policy), self.now, 'second'))
        self.assertFalse(guard.allow('overnight', self.policy, None, self.now, 'night'))
        self.now += 30
        self.record(distance='Nearby')
        guard.reset_home(self.policy, self.service.observe(self.policy), self.now)
        self.assertTrue(json.loads(path.read_text())['used'])
        self.record(location='Home', distance='Nearby')
        guard.reset_home(self.policy, self.service.observe(self.policy), self.now)
        self.assertFalse(json.loads(path.read_text())['used'])
        self.now += 30
        self.record()
        self.assertTrue(guard.allow('device_away', self.policy, self.service.observe(self.policy), self.now, 'next-trip'))

    def test_real_sqlite_to_signed_bridge_close_and_final_return_home_recheck(self):
        # Real HTTP authentication and storage, with only the physical relay mocked.
        self.record()
        path = self.root / 'policy.json'
        path.write_text(json.dumps(dict(enabled=True, timezone='America/Los_Angeles',
            away=dict(enabled=True, history_db=str(self.db), device_name='Example Phone', observer_at_home=True))))
        config = Config(device_id='fake', local_key='fake', address='127.0.0.1', protocol_version=3.3,
                        api_secret='s' * 32, host='127.0.0.1', port=0, auto_close_config=path)
        fake = Mock()
        with patch('gate_bridge.server.GateDevice', return_value=fake):
            server = GateHTTPServer(config)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            live = replace(config, port=server.server_port)
            with patch('gate_bridge.server.time.time', return_value=self.now), patch.object(server.position, 'observe', return_value=self.camera()):
                result = send_conditional_close(live, self.now - 30, 'a' * 32)
                self.assertTrue(result['success'])
                result = send_conditional_close(live, self.now - 30, 'c' * 32)
                self.assertEqual(result['reason'], 'away_trip_already_used')
                self.record(distance='Nearby')
                result = send_conditional_close(live, self.now - 30, 'b' * 32)
                self.assertEqual(result['result'], 'skipped')
            fake.pulse.assert_called_once()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
