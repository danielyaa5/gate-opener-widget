#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
from pathlib import Path


LABEL = "com.danielyakobian.gate-funnel-watchdog"
TAILSCALE_LABEL = "homebrew.mxcl.tailscale"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE = PROJECT_ROOT / "gate_bridge" / "watchdog.py"
INSTALL_DIR = Path("/usr/local/libexec")
EXECUTABLE = INSTALL_DIR / f"{LABEL}.py"
PLIST = Path("/Library/LaunchDaemons") / f"{LABEL}.plist"
LOG = Path("/var/log/gate-funnel-watchdog.log")


def run(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, check=check, text=True, capture_output=True)


def public_health_url(origin: str) -> str:
    if not origin.startswith("https://"):
        raise SystemExit("--public-url must be the bridge's https:// Funnel origin")
    return f"{origin.rstrip('/')}/health"


def main() -> None:
    parser = argparse.ArgumentParser(description="Install the Tailscale Funnel watchdog as a LaunchDaemon.")
    parser.add_argument("--public-url", required=True, help="Bridge Funnel origin, e.g. https://mac.tailnet.ts.net")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("Run this installer as root.")
    if not SOURCE.exists():
        raise SystemExit(f"Missing watchdog source: {SOURCE}")
    if not Path("/opt/homebrew/bin/tailscale").exists():
        raise SystemExit("Tailscale CLI is not installed at /opt/homebrew/bin/tailscale")

    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE, EXECUTABLE)
    os.chown(EXECUTABLE, 0, 0)
    os.chmod(EXECUTABLE, 0o755)

    payload = {
        "Label": LABEL,
        "ProgramArguments": [
            "/usr/bin/python3",
            str(EXECUTABLE),
            "--public-url",
            public_health_url(args.public_url),
            "--tailscale-path",
            "/opt/homebrew/bin/tailscale",
            "--service-label",
            TAILSCALE_LABEL,
            "--funnel-target",
            "8765",
        ],
        "RunAtLoad": True,
        "StartInterval": 60,
        "ProcessType": "Background",
        "StandardOutPath": str(LOG),
        "StandardErrorPath": str(LOG),
    }
    temporary = PLIST.with_suffix(".plist.tmp")
    temporary.write_bytes(plistlib.dumps(payload, sort_keys=False))
    os.chown(temporary, 0, 0)
    os.chmod(temporary, 0o644)
    temporary.replace(PLIST)

    service = f"system/{LABEL}"
    if run("/bin/launchctl", "print", service, check=False).returncode == 0:
        run("/bin/launchctl", "bootout", service)
    run("/bin/launchctl", "enable", service)
    run("/bin/launchctl", "bootstrap", "system", str(PLIST))
    run("/bin/launchctl", "kickstart", "-k", service)
    print(f"Installed and started {LABEL}.")


if __name__ == "__main__":
    main()
