from __future__ import annotations

import hashlib
import hmac
import threading
import time


class AuthenticationError(ValueError):
    pass


def signature_payload(
    timestamp: str,
    nonce: str,
    method: str,
    path: str,
    body: bytes,
) -> bytes:
    body_digest = hashlib.sha256(body).hexdigest()
    return f"{timestamp}\n{nonce}\n{method.upper()}\n{path}\n{body_digest}".encode()


def make_signature(
    secret: str,
    timestamp: str,
    nonce: str,
    method: str,
    path: str,
    body: bytes,
) -> str:
    return hmac.new(
        secret.encode(),
        signature_payload(timestamp, nonce, method, path, body),
        hashlib.sha256,
    ).hexdigest()


class RequestAuthenticator:
    def __init__(self, secret: str, max_clock_skew: float = 30.0) -> None:
        self._secret = secret
        self._max_clock_skew = max_clock_skew
        self._seen_nonces: dict[str, float] = {}
        self._lock = threading.Lock()

    def verify(
        self,
        *,
        timestamp: str | None,
        nonce: str | None,
        signature: str | None,
        method: str,
        path: str,
        body: bytes,
        now: float | None = None,
    ) -> None:
        if not timestamp or not nonce or not signature:
            raise AuthenticationError("Missing authentication headers")
        if len(nonce) < 16 or len(nonce) > 128:
            raise AuthenticationError("Invalid nonce")

        try:
            request_time = float(timestamp)
        except ValueError as exc:
            raise AuthenticationError("Invalid timestamp") from exc

        current_time = time.time() if now is None else now
        if abs(current_time - request_time) > self._max_clock_skew:
            raise AuthenticationError("Expired request")

        expected = make_signature(
            self._secret, timestamp, nonce, method, path, body
        )
        if not hmac.compare_digest(signature, expected):
            raise AuthenticationError("Invalid signature")

        with self._lock:
            cutoff = current_time - self._max_clock_skew
            self._seen_nonces = {
                key: seen_at
                for key, seen_at in self._seen_nonces.items()
                if seen_at >= cutoff
            }
            if nonce in self._seen_nonces:
                raise AuthenticationError("Replayed request")
            self._seen_nonces[nonce] = current_time
