from __future__ import annotations

import unittest

from gate_bridge.auth import AuthenticationError, RequestAuthenticator, make_signature


class RequestAuthenticatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.secret = "s" * 32
        self.auth = RequestAuthenticator(self.secret, max_clock_skew=30)
        self.timestamp = "1000"
        self.nonce = "0123456789abcdef"
        self.signature = make_signature(
            self.secret, self.timestamp, self.nonce, "POST", "/v1/open", b""
        )

    def verify(self, **overrides: object) -> None:
        values: dict[str, object] = {
            "timestamp": self.timestamp,
            "nonce": self.nonce,
            "signature": self.signature,
            "method": "POST",
            "path": "/v1/open",
            "body": b"",
            "now": 1000.0,
        }
        values.update(overrides)
        self.auth.verify(**values)  # type: ignore[arg-type]

    def test_accepts_valid_signature(self) -> None:
        self.verify()

    def test_rejects_replay(self) -> None:
        self.verify()
        with self.assertRaisesRegex(AuthenticationError, "Replayed"):
            self.verify()

    def test_rejects_expired_request(self) -> None:
        with self.assertRaisesRegex(AuthenticationError, "Expired"):
            self.verify(now=1031.0)

    def test_rejects_modified_path(self) -> None:
        with self.assertRaisesRegex(AuthenticationError, "Invalid signature"):
            self.verify(path="/v1/status")


if __name__ == "__main__":
    unittest.main()
