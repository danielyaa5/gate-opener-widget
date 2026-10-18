#!/usr/bin/env python3
"""Retrieve a Tuya local key without persisting cloud credentials."""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

import tinytuya


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"


def required_prompt(label: str) -> str:
    while True:
        value = input(label).strip()
        if value:
            return value
        print("A value is required.")


def main() -> int:
    print("Tuya one-time local-key setup")
    print("Your cloud secret is hidden while typing and is never saved to disk.\n")

    access_id = required_prompt("Tuya Access ID / Client ID: ")
    access_secret = getpass.getpass("Tuya Access Secret / Client Secret: ").strip()
    if not access_secret:
        print("A Client Secret is required.", file=sys.stderr)
        return 1

    device_id = required_prompt("Gate Device ID: ")
    region = input("Tuya region [us]: ").strip() or "us"
    address = required_prompt("Gate local IP: ")

    try:
        cloud = tinytuya.Cloud(
            apiRegion=region,
            apiKey=access_id,
            apiSecret=access_secret,
            apiDeviceID=device_id,
        )
        devices = cloud.getdevices()
    except Exception as exc:
        print(f"Tuya connection failed: {exc}", file=sys.stderr)
        return 1

    if not isinstance(devices, list):
        message = devices.get("Error", devices) if isinstance(devices, dict) else devices
        print(f"Tuya did not return a device list: {message}", file=sys.stderr)
        return 1

    target = next((device for device in devices if device.get("id") == device_id), None)
    if target is None:
        print("The specified device was not returned by this Tuya project.", file=sys.stderr)
        return 1

    local_key = target.get("key") or target.get("local_key")
    if not local_key:
        print("Tuya returned the device without a local key.", file=sys.stderr)
        return 1

    if ENV_PATH.exists():
        overwrite = input(f"{ENV_PATH} already exists. Replace it? [y/N]: ").strip().lower()
        if overwrite != "y":
            print("No file was changed.")
            return 1

    contents = (
        f"GATE_DEVICE_ID={device_id}\n"
        f"GATE_LOCAL_KEY={local_key}\n"
        f"GATE_ADDRESS={address}\n"
        "GATE_PROTOCOL_VERSION=3.5\n"
    )

    temporary_path = ENV_PATH.with_suffix(".env.tmp")
    temporary_path.write_text(contents, encoding="utf-8")
    os.chmod(temporary_path, 0o600)
    temporary_path.replace(ENV_PATH)

    print(f"\nLocal device credentials saved securely to {ENV_PATH}.")
    print("The Tuya cloud Client ID and Client Secret were not saved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
