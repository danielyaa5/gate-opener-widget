from __future__ import annotations

import threading
import time
import logging
from collections.abc import Callable

import tinytuya

from gate_bridge.config import Config


LOGGER = logging.getLogger("gate_bridge.device")


class GateBusyError(RuntimeError):
    pass


class GateCommandError(RuntimeError):
    pass


class GateDevice:
    def __init__(
        self,
        config: Config,
        *,
        device_factory: Callable[[], object] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._device_factory = device_factory or self._make_device
        self._monotonic = monotonic
        self._sleep = sleep
        self._pulse_lock = threading.Lock()
        self._device_lock = threading.Lock()
        self._last_pulse = float("-inf")
        self._stop_keepalive = threading.Event()
        self._keepalive_thread: threading.Thread | None = None
        self._device = self._new_device()

    def _new_device(self) -> object:
        device = self._device_factory()
        set_persistent = getattr(device, "set_socketPersistent", None)
        if callable(set_persistent):
            # This controller periodically leaves long-lived Tuya sockets in a
            # state where every request reports "Unable to Connect", while a
            # fresh socket succeeds immediately. Use one socket per command so
            # the bridge cannot remain trapped behind a stale connection.
            set_persistent(False)
        return device

    def _replace_device_locked(self) -> None:
        close = getattr(self._device, "close", None)
        if callable(close):
            try:
                close()
            except Exception as exc:
                LOGGER.warning("Could not close stale gate connection: %s", exc)
        self._device = self._new_device()

    def _make_device(self) -> tinytuya.Device:
        return tinytuya.Device(
            self._config.device_id,
            self._config.address,
            self._config.local_key,
            version=self._config.protocol_version,
            connection_timeout=3,
            connection_retry_limit=2,
        )

    @staticmethod
    def _raise_for_error(result: object, action: str) -> None:
        if isinstance(result, dict) and "Error" in result:
            raise GateCommandError(f"Gate {action} failed: {result['Error']}")

    def _validate_command(self, result: object, action: str) -> None:
        self._raise_for_error(result, action)
        # TinyTuya can return None for both an empty acknowledgement and a
        # missing response. Only a zero return code confirms the empty ACK.
        return_code = getattr(self._device, "cmd_retcode", None)
        LOGGER.info("Gate %s response type=%s return_code=%s", action, type(result).__name__, return_code)
        if return_code is not None and return_code != 0:
            raise GateCommandError(f"Gate {action} rejected by controller (code {return_code})")
        if result is None and return_code == 0:
            return
        if not isinstance(result, dict) or not result or result.get("success") is False:
            raise GateCommandError(
                f"Gate {action} was not confirmed. Check the gate before trying again."
            )

    def _command(self, value: bool, action: str) -> None:
        try:
            result = self._device.set_value(1, value)
            self._validate_command(result, action)
        except GateCommandError:
            raise
        except Exception as exc:
            raise GateCommandError(
                f"Gate {action} was not confirmed. Check the gate before trying again."
            ) from exc

    def status(self) -> dict[str, object]:
        with self._device_lock:
            # TinyTuya device instances can become unusable even when their
            # sockets are nonpersistent. Start every real operation with a new
            # client; never let an idle object become the request path.
            self._replace_device_locked()
            return self._status_locked()

    def _status_locked(self) -> dict[str, object]:
        last_error: GateCommandError | None = None
        for attempt in range(2):
            try:
                result = self._device.status()
                self._raise_for_error(result, "status query")
                if not isinstance(result, dict):
                    raise GateCommandError("Gate returned an invalid status response")
                return result
            except GateCommandError as exc:
                last_error = exc
            except Exception as exc:
                last_error = GateCommandError(f"Gate status query failed: {exc}")

            if attempt == 0:
                LOGGER.warning(
                    "Gate status failed; replacing the Tuya connection: %s",
                    last_error,
                )
                self._replace_device_locked()
                # The controller needs a brief quiet period after a failed
                # connection is closed before it will accept the replacement.
                self._sleep(1.0)

        assert last_error is not None
        raise last_error

    def start_keepalive(self, interval_seconds: float = 15) -> None:
        """Warm the Tuya socket and keep it ready for latency-sensitive commands."""
        if self._keepalive_thread is not None:
            return

        def keepalive() -> None:
            while not self._stop_keepalive.is_set():
                if self._device_lock.acquire(blocking=False):
                    try:
                        self._status_locked()
                    except Exception as exc:
                        LOGGER.warning("Gate keepalive failed: %s", exc)
                    finally:
                        self._device_lock.release()
                self._stop_keepalive.wait(interval_seconds)

        self._keepalive_thread = threading.Thread(
            target=keepalive,
            name="gate-tuya-keepalive",
            daemon=True,
        )
        self._keepalive_thread.start()

    def close(self) -> None:
        self._stop_keepalive.set()
        if self._keepalive_thread is not None:
            self._keepalive_thread.join(timeout=2)
        close = getattr(self._device, "close", None)
        if callable(close):
            close()

    def pulse(self) -> None:
        if not self._pulse_lock.acquire(blocking=False):
            raise GateBusyError("A gate pulse is already running")

        activated = False
        try:
            now = self._monotonic()
            if now - self._last_pulse < self._config.cooldown_seconds:
                raise GateBusyError("Gate pulse cooldown is active")

            with self._device_lock:
                # Use a new client for the state-changing request too. We do
                # not retry activation because the controller may have acted
                # even if its response was lost.
                self._replace_device_locked()
                try:
                    self._command(True, "activation")
                    activated = True
                    self._last_pulse = now
                    self._sleep(self._config.pulse_seconds)
                finally:
                    # Always issue a release. This is also safe if activation failed.
                    self._command(False, "release")
        finally:
            self._pulse_lock.release()

        if not activated:
            raise GateCommandError("Gate activation was not accepted")
