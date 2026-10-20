"""Request one camera-guarded close for an enabled overnight or TagTracker distance rule."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import time
import urllib.request
import uuid
from zoneinfo import ZoneInfo

from gate_bridge.auth import make_signature
from gate_bridge.away import AwayPolicy, AwayTripGuard, TagTrackerAwayService
from gate_bridge.config import Config
from gate_bridge.position import write_private_json

LOGGER = logging.getLogger("gate_bridge.auto_close")


@dataclass(frozen=True)
class AutoClosePolicy:
    enabled: bool = False
    start_hour: int = 21
    end_hour: int = 6
    timezone: str = "UTC"
    after_seconds: float = 2400
    max_image_age: float = 15
    minimum_match_score: float = 0.95
    away: AwayPolicy | None = None

    @classmethod
    def load(cls, path: Path | None) -> "AutoClosePolicy":
        if path is None:
            return cls()
        value = json.loads(path.read_text())
        policy = cls(**({key: value[key] for key in cls.__dataclass_fields__ if key in value and key != "away"}
                       | {"away": AwayPolicy.parse(value.get("away"))}))
        if type(policy.enabled) is not bool or not all(type(h) is int and 0 <= h <= 23 for h in (policy.start_hour, policy.end_hour)):
            raise ValueError("Invalid auto-close window")
        if (not all(type(x) in (int, float) and math.isfinite(x) for x in (policy.after_seconds, policy.max_image_age, policy.minimum_match_score))
                or policy.start_hour == policy.end_hour or not 2400 <= policy.after_seconds <= 86400
                or not 1 <= policy.max_image_age <= 30 or not 0.9 <= policy.minimum_match_score <= 1):
            raise ValueError("Invalid auto-close thresholds")
        try:
            ZoneInfo(policy.timezone)
        except (KeyError, TypeError) as exc:
            raise ValueError("Invalid auto-close time zone") from exc
        return policy

    def night(self, now: float) -> bool:
        hour = datetime.fromtimestamp(now, ZoneInfo(self.timezone)).hour
        return (self.start_hour <= hour < self.end_hour if self.start_hour < self.end_hour
                else hour >= self.start_hour or hour < self.end_hour)

    def trigger(self, observation: dict, now: float, away: dict | None = None) -> str | None:
        if (not isinstance(observation, dict) or not self.enabled
                or observation.get("state") != "open" or observation.get("reason") != "matched"):
            return None
        try:
            captured, checked, since, score = (float(observation[key]) for key in ("observed_at", "checked_at", "open_since", "match_score"))
            fresh = (all(math.isfinite(x) for x in (captured, checked, since, score))
                    and self.minimum_match_score <= score <= 1
                    and 0 <= now - captured <= self.max_image_age
                    and 0 <= now - checked <= self.max_image_age
                    and 0 <= since <= captured)
            if fresh:
                if self.away and self.away.eligible(away, now) and captured - since >= self.away.minimum_open_seconds:
                    return "device_away"
                if self.night(now) and captured - since >= self.after_seconds:
                    return "overnight"
        except (KeyError, ValueError, TypeError):
            pass
        return None

    def eligible(self, observation: dict, now: float, away: dict | None = None) -> bool:
        return self.trigger(observation, now, away) is not None


def send_conditional_close(config: Config, open_since: float, key: str) -> dict:
    path = "/v1/close-if-open"
    body = json.dumps({"idempotency_key": key, "open_since": open_since}).encode()
    stamp, nonce = str(time.time()), uuid.uuid4().hex
    request = urllib.request.Request(f"http://127.0.0.1:{config.port}{path}", data=body, method="POST", headers={
        "Content-Type": "application/json", "X-Gate-Timestamp": stamp, "X-Gate-Nonce": nonce,
        "X-Gate-Signature": make_signature(config.api_secret, stamp, nonce, "POST", path, body),
    })
    # Never retry: a lost response can still mean the gate moved.
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


class AutoCloser:
    def __init__(self, state_file: Path, sender, *, clock=time.time) -> None:
        self.path, self.sender, self.clock = state_file, sender, clock
        self.trip_guard = AwayTripGuard(state_file.with_suffix(".trip.json"))
        if state_file.exists():
            self.state = json.loads(state_file.read_text())
            if not isinstance(self.state, dict) or self.state.get("version") != 1 or type(self.state.get("attempted")) is not bool:
                raise ValueError("Invalid auto-close journal; manual review required")
        else:
            self.state = {"version": 1, "attempted": False, "status": "watching"}
        self.state.setdefault("away_latched", self.state.get("trigger") == "device_away")
        self.state.setdefault("away_latched_at", self.state.get("attempted_at", 0))
        if (type(self.state["away_latched"]) is not bool or
                (self.state["away_latched"] and not (type(self.state["away_latched_at"]) in (int, float)
                 and math.isfinite(self.state["away_latched_at"]) and self.state["away_latched_at"] >= 0))):
            raise ValueError("Invalid away trip journal; manual review required")

    def step(self, observation: dict, policy: AutoClosePolicy, away: dict | None = None) -> dict:
        if not isinstance(observation, dict):
            raise ValueError("Invalid camera observation")
        now = self.clock()
        if policy.away and policy.away.enabled:
            self.trip_guard.reset_home(policy.away, away, now)
            self.state["away"] = {key: away.get(key) for key in ("state", "reason", "observed_at", "distance_m", "distance_lower_m", "at_home")} if away else {"state": "unknown"}
            if (self.state["away_latched"] and policy.away.confirmed_home(away, now)
                    and away["observed_at"] > self.state["away_latched_at"]):
                self.state.update(away_latched=False, away_reset_at=away["observed_at"])
        state = self.state
        captured = observation.get("observed_at")
        checked = observation.get("checked_at")
        fresh_closed = (observation.get("state") == "closed" and observation.get("reason") == "matched"
                        and isinstance(captured, (int, float)) and math.isfinite(captured)
                        and isinstance(checked, (int, float)) and math.isfinite(checked)
                        and 0 <= now - checked <= policy.max_image_age
                        and 0 <= now - captured <= policy.max_image_age)
        if state["attempted"]:
            if fresh_closed and captured >= state["attempted_at"]:
                previous = state.get("last_closed_at")
                if previous is None or captured > previous:
                    state["closed_count"] = (state.get("closed_count", 0) + 1 if previous is None or captured - previous <= 30 else 1)
                    state["last_closed_at"] = captured
                if state.get("closed_count", 0) >= 3:
                    state.update(attempted=False, status="closed_confirmed", verified_at=now)
                    state.pop("closed_count", None)
                    state.pop("last_closed_at", None)
            elif not fresh_closed:
                state.pop("closed_count", None)
                state.pop("last_closed_at", None)
            if state["attempted"] and now - state["attempted_at"] > 120:
                state["status"] = "needs_attention"
        elif state["away_latched"]:
            state["status"] = "away_locked"
        elif policy.eligible(observation, now, away):
            state.update(attempted=True, attempted_at=now, open_since=observation["open_since"],
                         idempotency_key=uuid.uuid4().hex, status="command_pending", trigger=policy.trigger(observation, now, away))
            if state["trigger"] == "device_away":
                state.update(away_latched=True, away_latched_at=now)
            # Durable claim comes before transmission, including across restarts.
            write_private_json(self.path, state, durable=True)
            try:
                result = self.sender(float(observation["open_since"]), state["idempotency_key"])
                if result.get("result") == "skipped":
                    state.update(attempted=False, status="watching", skip_reason=result.get("reason"))
                elif result.get("success") is True and result.get("result") == "command_accepted":
                    state["status"] = "awaiting_closed"
                else:
                    state["status"] = "needs_attention"
            except Exception:
                state["status"] = "needs_attention"
        elif not policy.enabled:
            state["status"] = "disabled"
        else:
            state["status"] = "watching"
        state["checked_at"] = now
        state["gate_state"] = observation.get("state", "unknown")
        since = observation.get("open_since")
        state["current_open_since"] = since if type(since) in (int, float) and math.isfinite(since) else None
        write_private_json(self.path, state, durable=True)
        return dict(state)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--log-file", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    handlers = [RotatingFileHandler(args.log_file, maxBytes=2_000_000, backupCount=2)] if args.log_file else None
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s", handlers=handlers)
    bridge = Config.from_env()
    camera = json.loads(bridge.camera_config.read_text())
    observation_file = Path(camera["observation_file"])
    state_file = args.config.with_suffix(".state.json")
    with state_file.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        closer = AutoCloser(state_file, lambda since, key: send_conditional_close(bridge, since, key))
        devices = TagTrackerAwayService()
        last_status = None
        while True:
            try:
                policy = AutoClosePolicy.load(args.config)
                observation = json.loads(observation_file.read_text())
                result = closer.step(observation, policy, devices.observe(policy.away or AwayPolicy()))
                signature = (result["status"], result["gate_state"])
                if signature != last_status:
                    LOGGER.info("status=%s gate=%s", *signature)
                    last_status = signature
            except (OSError, ValueError, KeyError, TypeError):
                LOGGER.error("Auto-close configuration or observation unavailable; no command sent")
            time.sleep(10)


if __name__ == "__main__":
    main()
