"""AWS Lambda handler for the private "front gate" Alexa custom skill.

The skill sends one signed toggle command to the gate bridge per Alexa
request. It is deliberately a single standard-library file so it can be
deployed to Lambda without packaging dependencies.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone


LOGGER = logging.getLogger("alexa_gate")
LOGGER.setLevel(logging.INFO)

OPEN_PATH = "/v1/open"
# Alexa abandons a skill response after about eight seconds.
BRIDGE_TIMEOUT_SECONDS = 6.0
MAX_REQUEST_AGE_SECONDS = 150.0
OPEN_INTENT = "OpenGateIntent"

SPEECH = {
    "command_accepted": "Okay, I sent the gate signal.",
    "duplicate": "That request was already handled.",
    "busy": "The gate was just triggered. Try again in a few seconds.",
    "unknown": (
        "I couldn't confirm whether the gate received the signal. "
        "Check the gate before trying again."
    ),
    "not_sent": "I couldn't reach the gate. Nothing was sent.",
    "rejected": "The gate bridge refused the request.",
    "device_failed": (
        "The gate controller reported a problem. "
        "Check the gate before trying again."
    ),
    "unauthorized": "Sorry, this skill isn't available.",
    "help": "Say open, and I'll send the gate signal.",
    "goodbye": "Okay.",
}


def make_signature(
    secret: str, timestamp: str, nonce: str, method: str, path: str, body: bytes
) -> str:
    # Must stay identical to gate_bridge.auth.make_signature.
    body_digest = hashlib.sha256(body).hexdigest()
    payload = f"{timestamp}\n{nonce}\n{method.upper()}\n{path}\n{body_digest}"
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def correlation_id(alexa_request_id: str) -> str:
    """Stable, non-reversible ID shared by the Lambda and bridge logs."""
    return hashlib.sha256(alexa_request_id.encode()).hexdigest()[:24]


def _redacted(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def send_open(base_url: str, secret: str, request_id: str) -> tuple[str, int | None]:
    """Send exactly one open command. Never retried, whatever the outcome."""
    body = json.dumps({"idempotency_key": request_id}, separators=(",", ":")).encode()
    timestamp = str(time.time())
    nonce = secrets.token_hex(16)
    request = urllib.request.Request(
        base_url.rstrip("/") + OPEN_PATH,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Gate-Client": "alexa",
            "X-Gate-Request-ID": request_id,
            "X-Gate-Timestamp": timestamp,
            "X-Gate-Nonce": nonce,
            "X-Gate-Signature": make_signature(
                secret, timestamp, nonce, "POST", OPEN_PATH, body
            ),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=BRIDGE_TIMEOUT_SECONDS) as response:
            payload = json.load(response)
            if payload.get("result") == "command_accepted":
                return "command_accepted", response.status
            return "unknown", response.status
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            return "duplicate", exc.code
        if exc.code == 429:
            return "busy", exc.code
        if exc.code in (400, 401, 403, 404):
            return "rejected", exc.code
        if exc.code == 502:
            # The bridge reached the controller but the pulse was not
            # acknowledged; the relay may still have fired.
            return "device_failed", exc.code
        return "unknown", exc.code
    except urllib.error.URLError as exc:
        # A refused connection or failed DNS lookup happens before the request
        # leaves; anything else may have reached the bridge.
        if isinstance(exc.reason, (ConnectionRefusedError, socket.gaierror)):
            return "not_sent", None
        return "unknown", None
    except (TimeoutError, OSError, ValueError):
        return "unknown", None


def _response(speech: str) -> dict[str, object]:
    return {
        "version": "1.0",
        "response": {
            "outputSpeech": {"type": "PlainText", "text": speech},
            "shouldEndSession": True,
        },
    }


def _is_fresh(timestamp: object, now: float) -> bool:
    if not isinstance(timestamp, str):
        return False
    try:
        sent = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return False
    return abs(now - sent.timestamp()) <= MAX_REQUEST_AGE_SECONDS


def _log(**fields: object) -> None:
    LOGGER.info(json.dumps(fields, separators=(",", ":")))


def lambda_handler(event: dict, context: object, *, opener=send_open, now=None) -> dict:
    environment = os.environ
    system = event.get("context", {}).get("System", {})
    request = event.get("request", {})
    request_type = request.get("type", "")
    intent = request.get("intent", {}).get("name", "")
    request_id = correlation_id(str(request.get("requestId", "")))
    user_id = str(system.get("user", {}).get("userId", ""))
    fields = {
        "request_id": request_id,
        "type": request_type,
        "intent": intent,
        "user": _redacted(user_id),
    }

    allowed_users = {
        value.strip()
        for value in environment.get("ALEXA_ALLOWED_USER_IDS", "").split(",")
        if value.strip()
    }
    skill_id = environment.get("ALEXA_SKILL_ID", "")
    authorized = (
        bool(skill_id)
        and system.get("application", {}).get("applicationId") == skill_id
        and (not allowed_users or user_id in allowed_users)
        and _is_fresh(request.get("timestamp"), time.time() if now is None else now)
    )
    if not authorized:
        _log(**fields, outcome="unauthorized")
        return _response(SPEECH["unauthorized"])

    if request_type == "SessionEndedRequest":
        return {"version": "1.0", "response": {}}
    wants_open = request_type == "LaunchRequest" or (
        request_type == "IntentRequest" and intent == OPEN_INTENT
    )
    if not wants_open:
        if intent in ("AMAZON.CancelIntent", "AMAZON.StopIntent"):
            return _response(SPEECH["goodbye"])
        _log(**fields, outcome="no_action")
        return _response(SPEECH["help"])

    started = time.monotonic()
    outcome, http_status = opener(
        environment["GATE_BRIDGE_URL"], environment["GATE_ALEXA_API_SECRET"], request_id
    )
    _log(
        **fields,
        outcome=outcome,
        bridge_status=http_status,
        movement="unverified",
        elapsed_ms=round((time.monotonic() - started) * 1000),
    )
    return _response(SPEECH[outcome])
