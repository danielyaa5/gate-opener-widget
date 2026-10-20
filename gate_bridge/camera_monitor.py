"""Keep a shared gate observation fresh without interacting with the controller."""
from __future__ import annotations

import argparse
import asyncio
import base64
import fcntl
import json
import logging
import math
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import signal
import time

from gate_bridge.position import (CameraSnapshot, MAX_IMAGE_BYTES, ROOT, read_movement,
                                  save_preview, snapshot_observation, write_private_json)

LOGGER = logging.getLogger("gate_bridge.camera")


def track_open_duration(result: dict, previous: dict | None) -> None:
    result["open_since"] = None
    if result.get("state") != "open" or result.get("reason") != "matched":
        return
    captured = result["observed_at"]
    result["open_since"] = captured
    if not isinstance(previous, dict) or previous.get("state") != "open" or previous.get("reason") != "matched":
        return
    try:
        before = float(previous["observed_at"])
        since = float(previous["open_since"])
        if all(math.isfinite(x) for x in (before, since)) and 0 <= captured - before <= 30 and since <= before:
            result["open_since"] = since
    except (KeyError, ValueError, TypeError):
        pass


def save_snapshot_line(line: bytes, config_path: Path, *, now: float | None = None) -> float:
    """Consume a private worker message; return its next expected delay."""
    now = time.time() if now is None else now
    config = json.loads(config_path.read_text())
    output = Path(config["observation_file"])
    payload = json.loads(line)
    result = {"state": "unknown", "observed_at": None, "checked_at": now,
              "reason": "camera_unavailable", "match_score": None}
    interval = float(config.get("poll_interval_seconds", 10))
    retry_after = interval
    if "error" not in payload:
        data = base64.b64decode(payload["image"], validate=True)
        if len(data) > MAX_IMAGE_BYTES or not data.startswith(b"\xff\xd8"):
            raise ValueError("Invalid camera image")
        snapshot = CameraSnapshot(data, float(payload["captured_at"]))
        result = snapshot_observation(snapshot, config, now)
        save_preview(snapshot, config_path, config, now)
        movement = read_movement(output)
        if now < movement.get("moving_until", 0) or snapshot.captured_at < movement.get("command_at", 0):
            result.update(state="unknown", reason="movement", match_score=None)
    else:
        retry_after = max(interval, float(payload.get("retry_after", interval)))
    try:
        previous = json.loads(output.read_text())
    except (OSError, ValueError):
        previous = None
    track_open_duration(result, previous)
    write_private_json(output, result)
    # No image, camera identifier, credentials or request payload goes in logs.
    LOGGER.info("state=%s reason=%s image_age=%.1f next_check=%.1f",
                result["state"], result["reason"], now - result["observed_at"] if result["observed_at"] else -1, retry_after)
    return retry_after


async def watch_worker(config_path: Path, *, command: list[str] | None = None, max_messages: int | None = None) -> None:
    config = json.loads(config_path.read_text())
    args = command or [config.get("node", "node"), str(ROOT / "camera/snapshot.cjs"), str(config_path), "--watch"]
    process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.DEVNULL, limit=MAX_IMAGE_BYTES * 2)
    timeout = 45.0
    count = 0
    try:
        while True:
            line = await asyncio.wait_for(process.stdout.readline(), timeout)
            if not line:
                raise RuntimeError("Camera worker stopped")
            timeout = max(45.0, save_snapshot_line(line, config_path) + 30)
            count += 1
            if max_messages is not None and count >= max_messages:
                return
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()


async def run_monitor(config_path: Path) -> None:
    config = json.loads(config_path.read_text())
    output = Path(config["observation_file"])
    # A second monitor must never duplicate requests or race token rotation.
    with output.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        failures = 0
        while True:
            started = time.monotonic()
            try:
                await watch_worker(config_path)
            except (OSError, ValueError, KeyError, TypeError, ImportError, RuntimeError, asyncio.TimeoutError):
                if time.monotonic() - started > 60:
                    failures = 0
                failures += 1
                delay = min(300, 10 * 2 ** min(failures - 1, 5))
                write_private_json(output, {"state": "unknown", "observed_at": None,
                    "checked_at": time.time(), "reason": "camera_unavailable", "match_score": None})
                LOGGER.warning("Camera worker unavailable; restarting in %s seconds", delay)
                await asyncio.sleep(delay)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--log-file", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    handlers = [RotatingFileHandler(args.log_file, maxBytes=2_000_000, backupCount=2)] if args.log_file else None
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s", handlers=handlers)

    async def serve() -> None:
        task = asyncio.current_task()
        for signum in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_running_loop().add_signal_handler(signum, task.cancel)
        await run_monitor(args.config)
    try:
        asyncio.run(serve())
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    main()
