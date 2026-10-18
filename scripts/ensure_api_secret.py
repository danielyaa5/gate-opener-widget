#!/usr/bin/env python3
from __future__ import annotations

import os
import secrets
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"


def main() -> None:
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    if any(line.startswith("GATE_API_SECRET=") and line.split("=", 1)[1] for line in lines):
        print("GATE_API_SECRET is already configured.")
        return

    defaults = {
        "GATE_BRIDGE_HOST": "0.0.0.0",
        "GATE_BRIDGE_PORT": "8765",
        "GATE_PULSE_SECONDS": "0.5",
        "GATE_COOLDOWN_SECONDS": "3",
    }
    existing_keys = {line.split("=", 1)[0] for line in lines if "=" in line}
    values = {"GATE_API_SECRET": secrets.token_urlsafe(32)}
    values.update({key: value for key, value in defaults.items() if key not in existing_keys})
    for key, value in values.items():
        if key in existing_keys:
            lines = [f"{key}={value}" if line.startswith(f"{key}=") else line for line in lines]
        else:
            lines.append(f"{key}={value}")

    temporary = ENV_PATH.with_suffix(".env.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    os.chmod(temporary, 0o600)
    temporary.replace(ENV_PATH)
    print("Generated a private API secret in .env.")


if __name__ == "__main__":
    main()
