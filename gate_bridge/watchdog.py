from __future__ import annotations

import argparse
import json
import logging
import ssl
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence
from urllib.request import Request, urlopen


LOGGER = logging.getLogger("gate_bridge.watchdog")


@dataclass
class WatchdogState:
    consecutive_failures: int = 0
    last_restart: float = 0.0
    internet_offline_since: float = 0.0
    nordvpn_last_restart: float = 0.0
    funnel_restore_pending: bool = False

    @classmethod
    def load(cls, path: Path) -> "WatchdogState":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                consecutive_failures=int(payload.get("consecutive_failures", 0)),
                last_restart=float(payload.get("last_restart", 0)),
                internet_offline_since=float(
                    payload.get("internet_offline_since", 0)
                ),
                nordvpn_last_restart=float(
                    payload.get("nordvpn_last_restart", 0)
                ),
                funnel_restore_pending=bool(
                    payload.get("funnel_restore_pending", False)
                ),
            )
        except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError, ValueError):
            return cls()

    def save(self, path: Path) -> None:
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_text(
            json.dumps(
                {
                    "consecutive_failures": self.consecutive_failures,
                    "last_restart": self.last_restart,
                    "internet_offline_since": self.internet_offline_since,
                    "nordvpn_last_restart": self.nordvpn_last_restart,
                    "funnel_restore_pending": self.funnel_restore_pending,
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        temporary.replace(path)


def health_check(url: str, timeout: float) -> bool:
    request = Request(url, headers={"Cache-Control": "no-cache"})
    context = ssl.create_default_context()
    try:
        with urlopen(request, timeout=timeout, context=context) as response:
            payload = json.loads(response.read())
            return response.status == 200 and payload == {"status": "ok"}
    except Exception as exc:
        LOGGER.warning("Health check failed for %s: %s", url, exc)
        return False


def internet_check(url: str, timeout: float) -> bool:
    request = Request(url, headers={"Cache-Control": "no-cache"})
    context = ssl.create_default_context()
    try:
        with urlopen(request, timeout=timeout, context=context) as response:
            return 200 <= response.status < 400
    except Exception as exc:
        LOGGER.warning("Internet check failed for %s: %s", url, exc)
        return False


def run_command(arguments: Sequence[str]) -> bool:
    try:
        result = subprocess.run(
            arguments,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        LOGGER.error("Could not run command (%s): %s", " ".join(arguments), exc)
        return False
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        LOGGER.error("Command failed (%s): %s", " ".join(arguments), detail)
        return False
    return True


def vpn_status(service_name: str) -> str:
    try:
        result = subprocess.run(
            ["/usr/sbin/scutil", "--nc", "status", service_name],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        LOGGER.error("Could not read NordVPN status: %s", exc)
        return "unknown"
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        LOGGER.error("Could not read NordVPN status: %s", detail)
        return "unknown"
    return result.stdout.partition("\n")[0].strip().lower()


def check_and_recover(
    *,
    local_url: str,
    public_url: str,
    state_path: Path,
    tailscale_path: str,
    service_label: str,
    funnel_target: str = "8765",
    internet_url: str = "https://connectivitycheck.gstatic.com/generate_204",
    nordvpn_service: str = "NordVPN NordLynx",
    offline_threshold: float = 180,
    nordvpn_restart_cooldown: float = 900,
    failure_threshold: int = 3,
    restart_cooldown: float = 900,
    timeout: float = 5,
    probe: Callable[[str, float], bool] = health_check,
    internet_probe: Callable[[str, float], bool] = internet_check,
    nordvpn_status: Callable[[str], str] = vpn_status,
    command: Callable[[Sequence[str]], bool] = run_command,
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    state = WatchdogState.load(state_path)
    current_time = now()

    if not internet_probe(internet_url, timeout):
        if not state.internet_offline_since:
            state.internet_offline_since = current_time
            state.save(state_path)
            LOGGER.warning("Internet connection lost; starting outage timer")
            return "internet_failure_recorded"

        offline_duration = current_time - state.internet_offline_since
        if offline_duration < offline_threshold:
            state.save(state_path)
            LOGGER.warning(
                "Internet has been offline for %.0f seconds; waiting before recovery",
                offline_duration,
            )
            return "internet_waiting"

        status = nordvpn_status(nordvpn_service).lower()
        if status != "connecting":
            state.save(state_path)
            LOGGER.warning(
                "Internet remains offline, but NordVPN state is %s; not restarting",
                status,
            )
            return "internet_offline"

        if current_time - state.nordvpn_last_restart < nordvpn_restart_cooldown:
            state.save(state_path)
            LOGGER.warning("NordVPN is still connecting; restart cooldown is active")
            return "nordvpn_cooldown"

        LOGGER.error(
            "Internet has been offline for %.0f seconds and NordVPN is stuck "
            "connecting; restarting the VPN connection",
            offline_duration,
        )
        stopped = command(["/usr/sbin/scutil", "--nc", "stop", nordvpn_service])
        if stopped:
            sleep(3)
        restarted = stopped and command(
            ["/usr/sbin/scutil", "--nc", "start", nordvpn_service]
        )
        if restarted:
            state.nordvpn_last_restart = current_time
            result = "nordvpn_restarted"
        else:
            result = "nordvpn_restart_failed"
        state.save(state_path)
        return result

    if state.internet_offline_since:
        LOGGER.info(
            "Internet connection recovered after %.0f seconds",
            current_time - state.internet_offline_since,
        )
        state.internet_offline_since = 0

    if not probe(local_url, timeout):
        state.consecutive_failures = 0
        state.save(state_path)
        LOGGER.error("Local bridge is unhealthy; skipping Tailscale recovery")
        return "local_unhealthy"

    if state.funnel_restore_pending:
        LOGGER.warning("Restoring a pending Tailscale Funnel configuration")
        restored = command(
            [tailscale_path, "funnel", "--bg", "--yes", funnel_target]
        )
        if not restored:
            state.save(state_path)
            return "funnel_restore_pending"
        state.funnel_restore_pending = False
        state.save(state_path)
        sleep(3)
        if probe(public_url, timeout):
            state.consecutive_failures = 0
            state.save(state_path)
            LOGGER.info("Public Funnel recovered after pending configuration restore")
            return "funnel_restored"

    if probe(public_url, timeout):
        if state.consecutive_failures:
            LOGGER.info(
                "Public Funnel recovered after %s failure(s)",
                state.consecutive_failures,
            )
        state.consecutive_failures = 0
        state.save(state_path)
        return "healthy"

    state.consecutive_failures += 1
    if state.consecutive_failures < failure_threshold:
        LOGGER.warning(
            "Public Funnel failure %s of %s",
            state.consecutive_failures,
            failure_threshold,
        )
        state.save(state_path)
        return "failure_recorded"

    if current_time - state.last_restart < restart_cooldown:
        LOGGER.warning("Public Funnel remains unhealthy; restart cooldown is active")
        state.save(state_path)
        return "cooldown"

    LOGGER.warning("Attempting lightweight Tailscale network recovery")
    command([tailscale_path, "debug", "rebind"])
    command([tailscale_path, "debug", "restun"])
    sleep(3)
    if probe(public_url, timeout):
        state.consecutive_failures = 0
        state.save(state_path)
        LOGGER.info("Public Funnel recovered after rebind/restun")
        return "rebound"

    LOGGER.warning("Refreshing the Tailscale Funnel configuration")
    reset = command([tailscale_path, "funnel", "reset"])
    if reset:
        state.funnel_restore_pending = True
        state.save(state_path)
    restored = command(
        [tailscale_path, "funnel", "--bg", "--yes", funnel_target]
    )
    if restored:
        state.funnel_restore_pending = False
        state.save(state_path)
    sleep(3)
    if restored and probe(public_url, timeout):
        state.consecutive_failures = 0
        state.save(state_path)
        LOGGER.info("Public Funnel recovered after configuration refresh")
        return "funnel_refreshed"

    LOGGER.error("Restarting Tailscale after repeated public Funnel failures")
    restarted = command(
        ["/bin/launchctl", "kickstart", "-k", f"system/{service_label}"]
    )
    if restarted:
        sleep(5)
        # Reassert the mapping after the daemon comes back. This is harmless
        # when the configuration persisted and restores it if reset succeeded
        # but the first enable attempt raced daemon startup.
        restored = command(
            [tailscale_path, "funnel", "--bg", "--yes", funnel_target]
        )
        state.last_restart = current_time
        state.funnel_restore_pending = not restored
        if not restored:
            result = "funnel_restore_pending"
        else:
            sleep(3)
            if probe(public_url, timeout):
                state.consecutive_failures = 0
                result = "restarted"
            else:
                status = nordvpn_status(nordvpn_service).lower()
                if status != "connected":
                    LOGGER.error(
                        "Public Funnel is still unhealthy after restarting "
                        "Tailscale, but NordVPN state is %s",
                        status,
                    )
                    result = "tailscale_restarted_funnel_unhealthy"
                elif (
                    current_time - state.nordvpn_last_restart
                    < nordvpn_restart_cooldown
                ):
                    LOGGER.warning(
                        "Public Funnel is still unhealthy after restarting "
                        "Tailscale; NordVPN restart cooldown is active"
                    )
                    result = "nordvpn_cooldown"
                else:
                    LOGGER.error(
                        "Public Funnel is still unhealthy after restarting "
                        "Tailscale; restarting NordVPN"
                    )
                    stopped = command(
                        ["/usr/sbin/scutil", "--nc", "stop", nordvpn_service]
                    )
                    if stopped:
                        sleep(3)
                    vpn_restarted = stopped and command(
                        ["/usr/sbin/scutil", "--nc", "start", nordvpn_service]
                    )
                    if not vpn_restarted:
                        result = "nordvpn_restart_failed"
                    else:
                        state.nordvpn_last_restart = current_time
                        sleep(5)
                        command([tailscale_path, "debug", "rebind"])
                        command([tailscale_path, "debug", "restun"])
                        command(
                            [
                                tailscale_path,
                                "funnel",
                                "--bg",
                                "--yes",
                                funnel_target,
                            ]
                        )
                        sleep(3)
                        if probe(public_url, timeout):
                            state.consecutive_failures = 0
                            result = "recovered_after_nordvpn_restart"
                        else:
                            result = "nordvpn_restarted_funnel_unhealthy"
    else:
        result = "restart_failed"
    state.save(state_path)
    return result


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Recover a stuck NordVPN connection or Tailscale Funnel connectivity."
        )
    )
    parser.add_argument("--local-url", default="http://127.0.0.1:8765/health")
    parser.add_argument("--public-url", required=True)
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path("/var/run/gate-funnel-watchdog.json"),
    )
    parser.add_argument(
        "--tailscale-path",
        default="/opt/homebrew/bin/tailscale",
    )
    parser.add_argument(
        "--service-label",
        default="homebrew.mxcl.tailscale",
    )
    parser.add_argument(
        "--funnel-target",
        default="8765",
        help="Local port or URL exposed by Tailscale Funnel",
    )
    parser.add_argument(
        "--internet-url",
        default="https://connectivitycheck.gstatic.com/generate_204",
    )
    parser.add_argument("--nordvpn-service", default="NordVPN NordLynx")
    parser.add_argument("--offline-threshold", type=float, default=180)
    parser.add_argument("--nordvpn-restart-cooldown", type=float, default=900)
    parser.add_argument("--failure-threshold", type=int, default=3)
    parser.add_argument("--restart-cooldown", type=float, default=900)
    parser.add_argument("--timeout", type=float, default=5)
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    arguments = parse_arguments()
    result = check_and_recover(
        local_url=arguments.local_url,
        public_url=arguments.public_url,
        state_path=arguments.state_file,
        tailscale_path=arguments.tailscale_path,
        service_label=arguments.service_label,
        funnel_target=arguments.funnel_target,
        internet_url=arguments.internet_url,
        nordvpn_service=arguments.nordvpn_service,
        offline_threshold=arguments.offline_threshold,
        nordvpn_restart_cooldown=arguments.nordvpn_restart_cooldown,
        failure_threshold=arguments.failure_threshold,
        restart_cooldown=arguments.restart_cooldown,
        timeout=arguments.timeout,
    )
    LOGGER.info("Watchdog result: %s", result)


if __name__ == "__main__":
    main()
