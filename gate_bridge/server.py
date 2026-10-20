from __future__ import annotations

import json
import logging
import math
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlsplit

from gate_bridge.auth import AuthenticationError, RequestAuthenticator
from gate_bridge.auto_close import AutoClosePolicy
from gate_bridge.away import AwayTripGuard, TagTrackerAwayService
from gate_bridge.command_lock import CommandLock
from gate_bridge.config import Config
from gate_bridge.device import GateBusyError, GateCommandError, GateDevice
from gate_bridge.position import GatePositionService


LOGGER = logging.getLogger("gate_bridge")
CLIENT_LOGGER = logging.getLogger("gate_bridge.client")
CLIENT_LOG_PATH = Path.home() / "Library" / "Logs" / "gate-bridge.client.log"
CLIENT_LOG_BODY_LIMIT = 32768
CLIENT_LOG_EVENT_LIMIT = 50
CLIENT_LOG_OPERATIONS = {"open", "status", "position"}
CLIENT_LOG_OUTCOMES = {"started", "retrying", "succeeded", "rejected", "failed"}
DEFAULT_CLIENT = "default"
ALEXA_CLIENT = "alexa"
IDEMPOTENCY_WINDOW_SECONDS = 600.0


class DuplicateRequestError(RuntimeError):
    pass


def parse_idempotency_key(body: bytes) -> str | None:
    """Read the optional idempotency key from a signed open request body."""
    if not body:
        return None
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid open request JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Invalid open request payload")
    key = payload.get("idempotency_key")
    if key is None:
        return None
    if (
        not isinstance(key, str)
        or not 16 <= len(key) <= 64
        or not all(character.isalnum() or character == "-" for character in key)
    ):
        raise ValueError("Invalid idempotency key")
    return key


class IdempotencyGuard:
    """Remembers recent open requests so a redelivered command cannot pulse twice.

    A key is claimed before the gate is activated and is never released, even
    when the activation fails: the controller may have acted on a command whose
    response was lost, so the same request must not be attempted again.
    """

    def __init__(self, window_seconds: float = IDEMPOTENCY_WINDOW_SECONDS) -> None:
        self._window_seconds = window_seconds
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def claim(self, key: str, now: float | None = None) -> None:
        current_time = time.monotonic() if now is None else now
        with self._lock:
            cutoff = current_time - self._window_seconds
            self._seen = {
                seen_key: seen_at
                for seen_key, seen_at in self._seen.items()
                if seen_at >= cutoff
            }
            if key in self._seen:
                raise DuplicateRequestError("Open request was already processed")
            self._seen[key] = current_time


def parse_client_events(body: bytes) -> list[dict[str, object]]:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid diagnostic JSON") from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise ValueError("Invalid diagnostic payload")

    raw_events = payload["events"]
    if not 1 <= len(raw_events) <= CLIENT_LOG_EVENT_LIMIT:
        raise ValueError("Invalid diagnostic event count")

    events: list[dict[str, object]] = []
    for raw_event in raw_events:
        if not isinstance(raw_event, dict):
            raise ValueError("Invalid diagnostic event")

        event_id = raw_event.get("id")
        timestamp = raw_event.get("timestamp")
        component = raw_event.get("component")
        operation = raw_event.get("operation")
        outcome = raw_event.get("outcome")
        request_id = raw_event.get("requestID")
        error_domain = raw_event.get("errorDomain")
        error_code = raw_event.get("errorCode")
        http_status = raw_event.get("httpStatus")

        if not isinstance(event_id, str) or not 1 <= len(event_id) <= 64:
            raise ValueError("Invalid diagnostic event ID")
        if not isinstance(timestamp, (int, float)) or timestamp <= 0:
            raise ValueError("Invalid diagnostic timestamp")
        if not isinstance(component, str) or not 1 <= len(component) <= 128:
            raise ValueError("Invalid diagnostic component")
        if operation not in CLIENT_LOG_OPERATIONS:
            raise ValueError("Invalid diagnostic operation")
        if outcome not in CLIENT_LOG_OUTCOMES:
            raise ValueError("Invalid diagnostic outcome")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 64:
            raise ValueError("Invalid diagnostic request ID")
        if error_domain is not None and (
            not isinstance(error_domain, str) or len(error_domain) > 128
        ):
            raise ValueError("Invalid diagnostic error domain")
        if error_code is not None and not isinstance(error_code, int):
            raise ValueError("Invalid diagnostic error code")
        if http_status is not None and (
            not isinstance(http_status, int) or not 100 <= http_status <= 599
        ):
            raise ValueError("Invalid diagnostic HTTP status")

        events.append(
            {
                "id": event_id,
                "timestamp": timestamp,
                "component": component,
                "operation": operation,
                "outcome": outcome,
                "request_id": request_id,
                "error_domain": error_domain,
                "error_code": error_code,
                "http_status": http_status,
            }
        )

    return events


def configure_client_logging(path: Path = CLIENT_LOG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=5)
    handler.setFormatter(logging.Formatter("%(message)s"))
    CLIENT_LOGGER.handlers.clear()
    CLIENT_LOGGER.addHandler(handler)
    CLIENT_LOGGER.setLevel(logging.INFO)
    CLIENT_LOGGER.propagate = False


class GateHTTPServer(ThreadingHTTPServer):
    def __init__(self, config: Config) -> None:
        super().__init__((config.host, config.port), GateRequestHandler)
        # Each client signs with its own secret so one can be revoked without
        # affecting the others. Requests without a client header are the
        # iPhone app, widget, and controls.
        self.authenticators = {
            DEFAULT_CLIENT: RequestAuthenticator(config.api_secret)
        }
        if config.alexa_api_secret:
            self.authenticators[ALEXA_CLIENT] = RequestAuthenticator(
                config.alexa_api_secret
            )
        self.idempotency = IdempotencyGuard()
        self.gate = GateDevice(config)
        self.position = GatePositionService(config.camera_config)
        self.commands = CommandLock(config.camera_config)
        self.auto_close_config = config.auto_close_config
        # Do not poll the Tuya controller in the background. This model becomes
        # unreachable after repeated keepalive queries but reliably accepts
        # fresh, nonpersistent sessions when a real request arrives.

    def server_close(self) -> None:
        self.gate.close()
        super().server_close()


class GateRequestHandler(BaseHTTPRequestHandler):
    server: GateHTTPServer

    def log_message(self, format: str, *args: object) -> None:
        LOGGER.info("%s - %s", self.client_address[0], format % args)

    def _json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self, maximum_length: int = 4096) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length > maximum_length:
            raise ValueError("Request body is too large")
        return self.rfile.read(length)

    def _request_id(self) -> str:
        value = self.headers.get("X-Gate-Request-ID", "unknown")
        if 1 <= len(value) <= 64 and all(character.isalnum() or character == "-" for character in value):
            return value
        return "invalid"

    def _authenticate(self, body: bytes) -> str:
        path = urlsplit(self.path).path
        client = self.headers.get("X-Gate-Client", DEFAULT_CLIENT)
        authenticator = self.server.authenticators.get(client)
        if authenticator is None:
            raise AuthenticationError("Unknown client")
        authenticator.verify(
            timestamp=self.headers.get("X-Gate-Timestamp"),
            nonce=self.headers.get("X-Gate-Nonce"),
            signature=self.headers.get("X-Gate-Signature"),
            method=self.command,
            path=path,
            body=body,
        )
        return client

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/health":
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        if path not in {"/v1/status", "/v1/position", "/v1/camera"}:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return

        request_id = self._request_id()
        try:
            client = self._authenticate(b"")
            if path in {"/v1/position", "/v1/camera"}:
                if client != DEFAULT_CLIENT:
                    self._json(HTTPStatus.FORBIDDEN, {"error": "camera_access_not_allowed"})
                    return
                payload = self.server.position.camera_preview() if path == "/v1/camera" else self.server.position.observe()
                self._json(HTTPStatus.OK, payload)
                return
            status = self.server.gate.status()
            LOGGER.info("request_id=%s status succeeded", request_id)
            self._json(HTTPStatus.OK, {"online": True, "device": status})
        except AuthenticationError as exc:
            LOGGER.warning("request_id=%s status authentication failed: %s", request_id, exc)
            self._json(HTTPStatus.UNAUTHORIZED, {"error": str(exc)})
        except GateCommandError as exc:
            LOGGER.error("request_id=%s status device failed: %s", request_id, exc)
            self._json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path == "/v1/client-log":
            self._receive_client_logs()
            return
        if path not in {"/v1/open", "/v1/close-if-open"}:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return

        request_id = self._request_id()
        client = "unauthenticated"
        try:
            body = self._body()
            client = self._authenticate(body)
            idempotency_key = parse_idempotency_key(body)
            conditional = path == "/v1/close-if-open"
            if conditional and client != DEFAULT_CLIENT:
                self._json(HTTPStatus.FORBIDDEN, {"error": "auto_close_access_not_allowed"})
                return
            with self.server.commands.acquire():
                if conditional and idempotency_key is None:
                    raise ValueError("Conditional close requires an idempotency key")
                if idempotency_key is not None:
                    self.server.idempotency.claim(idempotency_key)
                if conditional:
                    expected_since = json.loads(body).get("open_since")
                    if type(expected_since) not in (int, float) or not math.isfinite(expected_since):
                        raise ValueError("Invalid open duration")
                    policy = AutoClosePolicy.load(self.server.auto_close_config)
                    observation = self.server.position.observe()
                    away = TagTrackerAwayService().observe(policy.away) if policy.away else None
                    now = time.time()
                    trigger = policy.trigger(observation, now, away)
                    if (trigger is None or
                            abs(float(observation["open_since"]) - expected_since) > 0.001):
                        self._json(HTTPStatus.OK, {"success": False, "result": "skipped", "reason": "conditions_changed"})
                        return
                    trip = AwayTripGuard(self.server.auto_close_config.with_suffix(".state.trip.json"))
                    if not trip.allow(trigger, policy.away, away, now, idempotency_key):
                        self._json(HTTPStatus.OK, {"success": False, "result": "skipped", "reason": "away_trip_already_used"})
                        return
                self.server.position.invalidate()
                self.server.gate.pulse()
            # The controller acknowledged the relay pulse. Nothing here
            # observes the gate itself, so movement is never claimed.
            LOGGER.info(
                "request_id=%s client=%s operation=%s succeeded "
                "result=command_accepted movement=unverified",
                request_id,
                client,
                "auto_close" if conditional else "open",
            )
            self._json(
                HTTPStatus.OK,
                {
                    "success": True,
                    "result": "command_accepted",
                    "movement_verified": False,
                },
            )
        except AuthenticationError as exc:
            LOGGER.warning("request_id=%s open authentication failed: %s", request_id, exc)
            self._json(HTTPStatus.UNAUTHORIZED, {"error": str(exc)})
        except ValueError as exc:
            LOGGER.warning("request_id=%s client=%s open request failed: %s", request_id, client, exc)
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except DuplicateRequestError as exc:
            LOGGER.warning("request_id=%s client=%s open duplicate ignored", request_id, client)
            self._json(HTTPStatus.CONFLICT, {"error": str(exc), "duplicate": True})
        except GateBusyError as exc:
            LOGGER.warning("request_id=%s client=%s open busy: %s", request_id, client, exc)
            self._json(HTTPStatus.TOO_MANY_REQUESTS, {"error": str(exc)})
        except GateCommandError as exc:
            LOGGER.error(
                "request_id=%s client=%s open device failed result=unknown: %s",
                request_id,
                client,
                exc,
            )
            self._json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})

    def _receive_client_logs(self) -> None:
        request_id = self._request_id()
        try:
            body = self._body(CLIENT_LOG_BODY_LIMIT)
            self._authenticate(body)
            events = parse_client_events(body)
            received_at = datetime.now(timezone.utc).isoformat()
            for event in events:
                CLIENT_LOGGER.info(
                    json.dumps(
                        {
                            "received_at": received_at,
                            "upload_request_id": request_id,
                            **event,
                        },
                        separators=(",", ":"),
                    )
                )
            LOGGER.info("request_id=%s stored %s client events", request_id, len(events))
            self._json(HTTPStatus.OK, {"stored": len(events)})
        except AuthenticationError as exc:
            LOGGER.warning("request_id=%s client log authentication failed: %s", request_id, exc)
            self._json(HTTPStatus.UNAUTHORIZED, {"error": str(exc)})
        except ValueError as exc:
            LOGGER.warning("request_id=%s client log rejected: %s", request_id, exc)
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    configure_client_logging()
    config = Config.from_env()
    server = GateHTTPServer(config)
    LOGGER.info("Gate bridge listening on %s:%s", config.host, config.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
