#!/usr/bin/env python3
from __future__ import annotations

import os
import plistlib
import pwd
import grp
import shutil
import subprocess
import time
from pathlib import Path


LABEL = "com.danielyakobian.gate-bridge"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DESTINATION = Path("/Library/LaunchDaemons") / f"{LABEL}.plist"


def run(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, check=check, text=True, capture_output=True)


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("Run this installer with sudo.")

    project_owner = PROJECT_ROOT.stat().st_uid
    account = pwd.getpwuid(project_owner)
    user_name = account.pw_name
    group_name = grp.getgrgid(account.pw_gid).gr_name
    home = Path(account.pw_dir)
    python = PROJECT_ROOT / ".venv" / "bin" / "python"
    env_file = PROJECT_ROOT / ".env"
    log_dir = home / "Library" / "Logs"

    if not python.exists():
        raise SystemExit("Missing .venv. Create it and install requirements first.")
    if not env_file.exists():
        raise SystemExit("Missing .env. Complete local credential setup first.")
    if env_file.stat().st_uid != project_owner:
        raise SystemExit(".env must be owned by the project account.")
    if env_file.stat().st_mode & 0o077:
        raise SystemExit(".env permissions must not grant group or other access.")

    log_dir.mkdir(parents=True, exist_ok=True)
    os.chown(log_dir, project_owner, account.pw_gid)

    payload = {
        "Label": LABEL,
        "ProgramArguments": [str(python), "-m", "gate_bridge.server"],
        "WorkingDirectory": str(PROJECT_ROOT),
        "UserName": user_name,
        "GroupName": group_name,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "ThrottleInterval": 10,
        "StandardOutPath": str(log_dir / "gate-bridge.log"),
        "StandardErrorPath": str(log_dir / "gate-bridge.error.log"),
    }

    temporary = DESTINATION.with_suffix(".plist.tmp")
    temporary.write_bytes(plistlib.dumps(payload, sort_keys=False))
    os.chown(temporary, 0, 0)
    os.chmod(temporary, 0o644)
    temporary.replace(DESTINATION)

    uid = project_owner
    old_service = f"gui/{uid}/{LABEL}"
    run("/bin/launchctl", "bootout", old_service, check=False)
    old_plist = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    if old_plist.exists():
        disabled = old_plist.with_name(
            f"{old_plist.name}.disabled-{time.strftime('%Y%m%d-%H%M%S')}"
        )
        shutil.move(old_plist, disabled)

    service = f"system/{LABEL}"
    if run("/bin/launchctl", "print", service, check=False).returncode == 0:
        run("/bin/launchctl", "bootout", service)
    run("/bin/launchctl", "enable", service)
    run("/bin/launchctl", "bootstrap", "system", str(DESTINATION))
    run("/bin/launchctl", "kickstart", "-k", service)
    print(f"Installed and started {LABEL} at boot as {user_name}.")


if __name__ == "__main__":
    main()
