"""Serialize relay commands across bridge listeners, including conditional close."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import threading

from gate_bridge.device import GateBusyError, GateCommandError


class CommandLock:
    def __init__(self, camera_config: Path | None) -> None:
        self.thread_lock = threading.Lock()
        self.path = None
        if camera_config:
            try:
                configuration = json.loads(camera_config.read_text())
                if configuration.get("observation_file"):
                    self.path = Path(configuration["observation_file"]).with_suffix(".command.lock")
            except (OSError, ValueError, TypeError, AttributeError):
                pass

    @contextmanager
    def acquire(self):
        if not self.thread_lock.acquire(blocking=False):
            raise GateBusyError("A gate command is already running")
        descriptor = None
        try:
            if self.path:
                try:
                    descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise GateBusyError("A gate command is already running") from exc
                except OSError as exc:
                    raise GateCommandError("Gate command lock unavailable") from exc
            yield
        finally:
            if descriptor is not None:
                os.close(descriptor)
            self.thread_lock.release()
