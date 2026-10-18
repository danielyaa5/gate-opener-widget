from __future__ import annotations

import unittest

from gate_bridge.config import Config
from gate_bridge.device import GateBusyError, GateCommandError, GateDevice


class FakeDevice:
    def __init__(self, calls: list[tuple[int, bool]]) -> None:
        self.calls = calls
        self.persistent = False
        self.closed = False
        self.status_result: object = {"dps": {"1": False}}

    def set_socketPersistent(self, persistent: bool) -> None:
        self.persistent = persistent

    def set_value(self, dp: int, value: bool) -> dict[str, object]:
        self.calls.append((dp, value))
        return {"dps": {str(dp): value}}

    def status(self) -> dict[str, object]:
        return self.status_result  # type: ignore[return-value]

    def close(self) -> None:
        self.closed = True


class GateDeviceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[tuple[int, bool]] = []
        self.devices: list[FakeDevice] = []
        self.new_device_status_result: object = {"dps": {"1": False}}
        self.now = 100.0
        config = Config(
            device_id="device",
            local_key="local-key",
            address="127.0.0.1",
            protocol_version=3.5,
            api_secret="s" * 32,
            pulse_seconds=0.5,
            cooldown_seconds=3,
        )
        def make_device() -> FakeDevice:
            device = FakeDevice(self.calls)
            device.status_result = self.new_device_status_result
            self.devices.append(device)
            return device

        self.gate = GateDevice(
            config,
            device_factory=make_device,
            monotonic=lambda: self.now,
            sleep=lambda _: None,
        )

    def test_pulse_activates_then_releases(self) -> None:
        self.gate.pulse()
        self.assertEqual(self.calls, [(1, True), (1, False)])

    def test_pulse_uses_nonpersistent_device_connection(self) -> None:
        self.gate.pulse()

        self.assertEqual(len(self.devices), 2)
        self.assertTrue(self.devices[0].closed)
        self.assertFalse(self.devices[1].persistent)

    def test_status_uses_fresh_device_connection(self) -> None:
        self.gate.status()
        self.gate.status()
        self.assertEqual(len(self.devices), 3)
        self.assertTrue(self.devices[0].closed)
        self.assertTrue(self.devices[1].closed)

    def test_status_replaces_a_stale_connection_and_retries_once(self) -> None:
        results = iter([
            {"Error": "Network Error: Unable to Connect"},
            {"dps": {"1": False}},
        ])

        def make_device() -> FakeDevice:
            device = FakeDevice(self.calls)
            device.status_result = next(results)
            self.devices.append(device)
            return device

        self.gate._device_factory = make_device
        result = self.gate.status()

        self.assertEqual(result, {"dps": {"1": False}})
        self.assertEqual(len(self.devices), 3)
        self.assertTrue(self.devices[0].closed)
        self.assertFalse(self.devices[2].persistent)

    def test_status_reports_failure_after_replacement_also_fails(self) -> None:
        failure = {"Error": "Network Error: Unable to Connect"}
        self.devices[0].status_result = failure
        self.new_device_status_result = failure

        with self.assertRaisesRegex(GateCommandError, "Unable to Connect"):
            self.gate.status()

        self.assertEqual(len(self.devices), 3)

    def test_cooldown_rejects_second_pulse_but_still_releases(self) -> None:
        self.gate.pulse()
        with self.assertRaisesRegex(GateBusyError, "cooldown"):
            self.gate.pulse()
        self.assertEqual(self.calls[-1], (1, False))


if __name__ == "__main__":
    unittest.main()
