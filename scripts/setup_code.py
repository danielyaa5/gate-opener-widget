#!/usr/bin/env python3
"""Print a setup code that connects the Mac and iPhone apps to this bridge.

Paste the code into the app's Bridge screen. It contains the API secret, so copy it
only to your own devices and do not post it anywhere.
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from gate_bridge.config import Config  # noqa: E402

PREFIX = "gate1:"


def validate_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise argparse.ArgumentTypeError("Use a complete http:// or https:// bridge URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise argparse.ArgumentTypeError("Use only the bridge origin, without credentials, path, query, or fragment")
    return value.rstrip("/")


def encode(url: str, secret: str) -> str:
    payload = json.dumps({"url": url, "secret": secret}, separators=(",", ":")).encode()
    return PREFIX + base64.urlsafe_b64encode(payload).decode().rstrip("=")


def main() -> None:
    parser = argparse.ArgumentParser(description="Print a setup code for the Gate Opener apps.")
    parser.add_argument("--url", type=validate_url, help="Bridge HTTPS origin (defaults to this Mac's local hostname)")
    args = parser.parse_args()
    config = Config.from_env()
    if args.url:
        url = args.url
    else:
        host = subprocess.run(
            ["scutil", "--get", "LocalHostName"], check=True, capture_output=True, text=True
        ).stdout.strip()
        url = f"http://{host}.local:{config.port}"
    print("Paste this code into Gate Opener's Bridge screen. It contains your API secret; keep it private.")
    print(encode(url, config.api_secret))


if __name__ == "__main__":
    main()
