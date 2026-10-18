#!/usr/bin/env python3
from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path


PMSET = "/usr/bin/pmset"
POWER_LABEL = "com.danielyakobian.gate-power-policy"
POWER_PLIST = Path("/Library/LaunchDaemons") / f"{POWER_LABEL}.plist"


def run(*arguments: str) -> None:
    subprocess.run(arguments, check=True)


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("Run this installer with sudo.")

    # `sleep 0` only disables idle sleep; it does not override lid/manual sleep.
    # This Mac is a stationary, always-on gate server, so close that gap as well.
    run(PMSET, "-a", "sleep", "0")
    run(PMSET, "-a", "disksleep", "0")
    run(PMSET, "-a", "tcpkeepalive", "1")
    run(PMSET, "-a", "womp", "1")
    run(PMSET, "-a", "disablesleep", "1")

    # `disablesleep` can be reset by a reboot or macOS update. Reassert it at
    # boot and hourly so this dedicated server cannot silently return to sleep.
    payload = {
        "Label": POWER_LABEL,
        "ProgramArguments": [PMSET, "-a", "disablesleep", "1"],
        "RunAtLoad": True,
        "StartInterval": 3600,
        "ProcessType": "Background",
    }
    temporary = POWER_PLIST.with_suffix(".plist.tmp")
    temporary.write_bytes(plistlib.dumps(payload, sort_keys=False))
    os.chown(temporary, 0, 0)
    os.chmod(temporary, 0o644)
    temporary.replace(POWER_PLIST)

    service = f"system/{POWER_LABEL}"
    subprocess.run(["/bin/launchctl", "bootout", service], check=False)
    run("/bin/launchctl", "enable", service)
    run("/bin/launchctl", "bootstrap", "system", str(POWER_PLIST))
    run("/bin/launchctl", "kickstart", "-k", service)

    print("Configured this Mac as an always-awake gate server at boot and hourly.")
    print("Keep it connected to power, stationary, and adequately ventilated.")
    subprocess.run([PMSET, "-g"], check=True)


if __name__ == "__main__":
    main()
