#!/usr/bin/env python3
from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path
from xml.sax.saxutils import escape


LABEL = "com.danielyakobian.gate-bridge"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = PROJECT_ROOT / "launchd" / f"{LABEL}.plist.template"
DESTINATION = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG_DIR = Path.home() / "Library" / "Logs"


def run(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, check=check, text=True, capture_output=True)


def main() -> None:
    python = PROJECT_ROOT / ".venv" / "bin" / "python"
    env_file = PROJECT_ROOT / ".env"
    if not python.exists():
        raise SystemExit("Missing .venv. Create it and install requirements first.")
    if not env_file.exists():
        raise SystemExit("Missing .env. Complete local credential setup first.")

    contents = TEMPLATE.read_text(encoding="utf-8")
    contents = contents.replace("__PROJECT_ROOT__", escape(str(PROJECT_ROOT)))
    contents = contents.replace("__LOG_DIR__", escape(str(LOG_DIR)))
    plistlib.loads(contents.encode())

    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    temporary = DESTINATION.with_suffix(".plist.tmp")
    temporary.write_text(contents, encoding="utf-8")
    os.chmod(temporary, 0o644)
    temporary.replace(DESTINATION)

    domain = f"gui/{os.getuid()}"
    service = f"{domain}/{LABEL}"
    if run("launchctl", "print", service, check=False).returncode == 0:
        run("launchctl", "bootout", service)
    run("launchctl", "enable", service)
    run("launchctl", "bootstrap", domain, str(DESTINATION))
    run("launchctl", "kickstart", "-k", service)
    print(f"Installed and started {LABEL} from {PROJECT_ROOT}.")


if __name__ == "__main__":
    main()
