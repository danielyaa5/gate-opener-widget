from __future__ import annotations

import json
import os
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from alexa_skill import lambda_function
from gate_bridge import auth


SKILL_ID = "amzn1.ask.skill.test"
SECRET = "a" * 32
NOW = 1_800_000_000.0


class FakeOpener:
    def __init__(self, outcome: str = "command_accepted", status: int | None = 200):
        self.calls: list[tuple[str, str, str]] = []
        self.result = (outcome, status)

    def __call__(self, base_url: str, secret: str, request_id: str):
        self.calls.append((base_url, secret, request_id))
        return self.result


def event(
    request_type: str = "LaunchRequest",
    *,
    intent: str | None = None,
    skill_id: str = SKILL_ID,
    user_id: str = "user-1",
    age: float = 0,
) -> dict:
    request: dict[str, object] = {
        "type": request_type,
        "requestId": "amzn1.echo-api.request.1234",
        "timestamp": datetime.fromtimestamp(NOW - age, timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
    }
    if intent:
        request["intent"] = {"name": intent, "slots": {}}
    return {
        "context": {
            "System": {
                "application": {"applicationId": skill_id},
                "user": {"userId": user_id},
            }
        },
        "request": request,
    }


class AlexaSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        environment = patch.dict(
            os.environ,
            {
                "ALEXA_SKILL_ID": SKILL_ID,
                "GATE_BRIDGE_URL": "https://bridge.example",
                "GATE_ALEXA_API_SECRET": SECRET,
            },
            clear=True,
        )
        environment.start()
        self.addCleanup(environment.stop)
        self.opener = FakeOpener()

    def handle(self, alexa_event: dict) -> str:
        response = lambda_function.lambda_handler(
            alexa_event, None, opener=self.opener, now=NOW
        )
        return response["response"].get("outputSpeech", {}).get("text", "")

    def test_signature_matches_bridge(self) -> None:
        arguments = (SECRET, "1000.5", "n" * 32, "POST", "/v1/open", b'{"a":1}')
        self.assertEqual(
            lambda_function.make_signature(*arguments), auth.make_signature(*arguments)
        )

    def test_launch_sends_one_open(self) -> None:
        speech = self.handle(event())

        self.assertEqual(len(self.opener.calls), 1)
        self.assertEqual(speech, lambda_function.SPEECH["command_accepted"])
        self.assertNotIn("opened", speech)

    def test_open_intent_sends_one_open(self) -> None:
        self.handle(event("IntentRequest", intent="OpenGateIntent"))

        self.assertEqual(len(self.opener.calls), 1)

    def test_same_alexa_request_reuses_the_idempotency_key(self) -> None:
        self.handle(event())
        self.handle(event())

        first, second = self.opener.calls
        self.assertEqual(first[2], second[2])
        self.assertNotIn("amzn1", first[2])

    def test_other_skill_is_rejected(self) -> None:
        self.handle(event(skill_id="amzn1.ask.skill.other"))

        self.assertEqual(self.opener.calls, [])

    def test_stale_request_is_rejected(self) -> None:
        self.handle(event(age=600))

        self.assertEqual(self.opener.calls, [])

    def test_user_allowlist_is_enforced(self) -> None:
        os.environ["ALEXA_ALLOWED_USER_IDS"] = "user-1"
        self.handle(event(user_id="user-2"))
        self.assertEqual(self.opener.calls, [])

        self.handle(event(user_id="user-1"))
        self.assertEqual(len(self.opener.calls), 1)

    def test_help_and_stop_do_not_open(self) -> None:
        for intent in ("AMAZON.HelpIntent", "AMAZON.StopIntent", "AMAZON.FallbackIntent"):
            self.handle(event("IntentRequest", intent=intent))
        self.handle(event("SessionEndedRequest"))

        self.assertEqual(self.opener.calls, [])

    def test_ambiguous_result_is_not_reported_as_success(self) -> None:
        self.opener.result = ("unknown", None)

        speech = self.handle(event())

        self.assertEqual(len(self.opener.calls), 1)
        self.assertEqual(speech, lambda_function.SPEECH["unknown"])

    def test_logs_omit_secret_and_raw_identifiers(self) -> None:
        with self.assertLogs("alexa_gate", level="INFO") as captured:
            self.handle(event())

        output = "\n".join(captured.output)
        self.assertIn("command_accepted", output)
        self.assertIn('"movement":"unverified"', output)
        for private in (SECRET, "user-1", "amzn1.echo-api.request.1234"):
            self.assertNotIn(private, output)


class SendOpenTests(unittest.TestCase):
    def test_timeout_is_ambiguous_and_not_retried(self) -> None:
        with patch.object(
            lambda_function.urllib.request, "urlopen", side_effect=TimeoutError()
        ) as urlopen:
            result = lambda_function.send_open("https://bridge.example", SECRET, "r" * 24)

        self.assertEqual(result, ("unknown", None))
        self.assertEqual(urlopen.call_count, 1)

    def test_request_is_signed_without_secret_in_url(self) -> None:
        captured = {}

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *arguments):
                return False

            def read(self, *arguments):
                return json.dumps({"result": "command_accepted"}).encode()

        def urlopen(request, timeout):
            captured["request"] = request
            return Response()

        with patch.object(lambda_function.urllib.request, "urlopen", urlopen):
            result = lambda_function.send_open("https://bridge.example/", SECRET, "r" * 24)

        request = captured["request"]
        self.assertEqual(result, ("command_accepted", 200))
        self.assertEqual(request.full_url, "https://bridge.example/v1/open")
        self.assertEqual(request.get_header("X-gate-client"), "alexa")
        self.assertEqual(json.loads(request.data), {"idempotency_key": "r" * 24})
        self.assertNotIn(SECRET, request.full_url)


if __name__ == "__main__":
    unittest.main()
