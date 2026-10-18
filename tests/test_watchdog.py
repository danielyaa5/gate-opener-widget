from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Sequence

from gate_bridge.watchdog import WatchdogState, check_and_recover


class FunnelWatchdogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.state_path = Path(self.temporary_directory.name) / "state.json"
        self.commands: list[list[str]] = []

    def command(self, arguments: Sequence[str]) -> bool:
        self.commands.append(list(arguments))
        return True

    def run_watchdog(
        self,
        probe_results: list[bool],
        *,
        now: float = 1000,
        internet_online: bool = True,
        nordvpn_status: str = "connected",
    ) -> str:
        results = iter(probe_results)
        return check_and_recover(
            local_url="http://local/health",
            public_url="https://public/health",
            state_path=self.state_path,
            tailscale_path="/tailscale",
            service_label="tailscale.service",
            failure_threshold=3,
            restart_cooldown=900,
            probe=lambda _url, _timeout: next(results),
            internet_probe=lambda _url, _timeout: internet_online,
            nordvpn_status=lambda _service: nordvpn_status,
            command=self.command,
            now=lambda: now,
            sleep=lambda _seconds: None,
        )

    def test_healthy_public_endpoint_resets_failures(self) -> None:
        WatchdogState(consecutive_failures=2).save(self.state_path)

        self.assertEqual(self.run_watchdog([True, True]), "healthy")
        self.assertEqual(WatchdogState.load(self.state_path).consecutive_failures, 0)
        self.assertEqual(self.commands, [])

    def test_local_failure_never_restarts_tailscale(self) -> None:
        WatchdogState(consecutive_failures=2).save(self.state_path)

        self.assertEqual(self.run_watchdog([False]), "local_unhealthy")
        self.assertEqual(WatchdogState.load(self.state_path).consecutive_failures, 0)
        self.assertEqual(self.commands, [])

    def test_requires_three_consecutive_public_failures(self) -> None:
        self.assertEqual(self.run_watchdog([True, False]), "failure_recorded")
        self.assertEqual(self.run_watchdog([True, False]), "failure_recorded")
        self.assertEqual(self.commands, [])

        self.assertEqual(
            self.run_watchdog([True, False, False, False, True]),
            "restarted",
        )
        self.assertEqual(
            self.commands,
            [
                ["/tailscale", "debug", "rebind"],
                ["/tailscale", "debug", "restun"],
                ["/tailscale", "funnel", "reset"],
                ["/tailscale", "funnel", "--bg", "--yes", "8765"],
                [
                    "/bin/launchctl",
                    "kickstart",
                    "-k",
                    "system/tailscale.service",
                ],
                ["/tailscale", "funnel", "--bg", "--yes", "8765"],
            ],
        )

    def test_rebind_can_avoid_a_daemon_restart(self) -> None:
        WatchdogState(consecutive_failures=2).save(self.state_path)

        self.assertEqual(self.run_watchdog([True, False, True]), "rebound")
        self.assertEqual(len(self.commands), 2)
        self.assertNotIn("/bin/launchctl", self.commands[-1])

    def test_funnel_refresh_can_avoid_a_daemon_restart(self) -> None:
        WatchdogState(consecutive_failures=2).save(self.state_path)

        self.assertEqual(
            self.run_watchdog([True, False, False, True]),
            "funnel_refreshed",
        )
        self.assertEqual(
            self.commands,
            [
                ["/tailscale", "debug", "rebind"],
                ["/tailscale", "debug", "restun"],
                ["/tailscale", "funnel", "reset"],
                ["/tailscale", "funnel", "--bg", "--yes", "8765"],
            ],
        )

    def test_failed_funnel_reset_still_restores_the_mapping(self) -> None:
        WatchdogState(consecutive_failures=2).save(self.state_path)
        probe_results = iter([True, False, False, False, True])

        def partially_failing_command(arguments: Sequence[str]) -> bool:
            self.commands.append(list(arguments))
            return list(arguments) != ["/tailscale", "funnel", "reset"]

        self.assertEqual(
            check_and_recover(
                local_url="http://local/health",
                public_url="https://public/health",
                state_path=self.state_path,
                tailscale_path="/tailscale",
                service_label="tailscale.service",
                failure_threshold=3,
                restart_cooldown=900,
                probe=lambda _url, _timeout: next(probe_results),
                internet_probe=lambda _url, _timeout: True,
                command=partially_failing_command,
                now=lambda: 1000,
                sleep=lambda _seconds: None,
            ),
            "restarted",
        )
        self.assertIn(
            ["/tailscale", "funnel", "--bg", "--yes", "8765"],
            self.commands,
        )

    def test_persistent_funnel_failure_restarts_connected_nordvpn(self) -> None:
        WatchdogState(consecutive_failures=2).save(self.state_path)

        self.assertEqual(
            self.run_watchdog([True, False, False, False, False, True]),
            "recovered_after_nordvpn_restart",
        )
        self.assertEqual(
            self.commands[-5:],
            [
                ["/usr/sbin/scutil", "--nc", "stop", "NordVPN NordLynx"],
                ["/usr/sbin/scutil", "--nc", "start", "NordVPN NordLynx"],
                ["/tailscale", "debug", "rebind"],
                ["/tailscale", "debug", "restun"],
                ["/tailscale", "funnel", "--bg", "--yes", "8765"],
            ],
        )
        state = WatchdogState.load(self.state_path)
        self.assertEqual(state.consecutive_failures, 0)
        self.assertEqual(state.nordvpn_last_restart, 1000)

    def test_failed_restore_is_retried_on_the_next_run(self) -> None:
        WatchdogState(
            consecutive_failures=3,
            funnel_restore_pending=True,
        ).save(self.state_path)

        self.assertEqual(
            self.run_watchdog([True, True]),
            "funnel_restored",
        )
        self.assertEqual(
            self.commands,
            [["/tailscale", "funnel", "--bg", "--yes", "8765"]],
        )
        state = WatchdogState.load(self.state_path)
        self.assertFalse(state.funnel_restore_pending)
        self.assertEqual(state.consecutive_failures, 0)

    def test_restart_cooldown_prevents_a_loop(self) -> None:
        WatchdogState(consecutive_failures=2, last_restart=500).save(self.state_path)

        self.assertEqual(self.run_watchdog([True, False], now=1000), "cooldown")
        self.assertEqual(self.commands, [])

    def test_offline_for_three_minutes_restarts_connecting_nordvpn(self) -> None:
        self.assertEqual(
            self.run_watchdog([], now=1000, internet_online=False),
            "internet_failure_recorded",
        )
        self.assertEqual(
            self.run_watchdog([], now=1179, internet_online=False),
            "internet_waiting",
        )
        self.assertEqual(
            self.run_watchdog(
                [], now=1180, internet_online=False, nordvpn_status="Connecting"
            ),
            "nordvpn_restarted",
        )
        self.assertEqual(
            self.commands,
            [
                ["/usr/sbin/scutil", "--nc", "stop", "NordVPN NordLynx"],
                ["/usr/sbin/scutil", "--nc", "start", "NordVPN NordLynx"],
            ],
        )

    def test_offline_does_not_restart_nordvpn_unless_connecting(self) -> None:
        WatchdogState(internet_offline_since=800).save(self.state_path)

        self.assertEqual(
            self.run_watchdog(
                [], now=1000, internet_online=False, nordvpn_status="Disconnected"
            ),
            "internet_offline",
        )
        self.assertEqual(self.commands, [])

    def test_nordvpn_restart_has_a_cooldown(self) -> None:
        WatchdogState(
            internet_offline_since=700,
            nordvpn_last_restart=500,
        ).save(self.state_path)

        self.assertEqual(
            self.run_watchdog(
                [], now=1000, internet_online=False, nordvpn_status="Connecting"
            ),
            "nordvpn_cooldown",
        )
        self.assertEqual(self.commands, [])

    def test_recovered_internet_clears_outage_timer(self) -> None:
        WatchdogState(internet_offline_since=800).save(self.state_path)

        self.assertEqual(self.run_watchdog([True, True], now=1000), "healthy")
        self.assertEqual(WatchdogState.load(self.state_path).internet_offline_since, 0)


if __name__ == "__main__":
    unittest.main()
