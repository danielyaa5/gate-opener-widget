"""Read a recent TagTracker Devices observation from a stationary home Mac.

Screen observations are not fresh GPS fixes. Only an explicit Now age and an
unambiguous, conservatively rounded distance can permit a close.
"""
from __future__ import annotations
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime
import json
import fcntl
import math
from pathlib import Path
import re
import sqlite3
import time

from gate_bridge.position import write_private_json

MILE_METERS = 1609.344
SOURCE = "tagtracker.findmy.devices.v1"


def finite_number(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


@dataclass(frozen=True)
class AwayPolicy:
    enabled: bool = False
    history_db: str | None = None
    device_name: str | None = None
    observer_at_home: bool = False
    origin_uncertainty_m: float = 200
    radius_m: float = MILE_METERS
    max_scan_age: float = 90
    minimum_confidence: float = 0.9
    minimum_open_seconds: float = 20
    home_label: str = "Home"
    home_radius_m: float = 400

    @classmethod
    def parse(cls, value: dict | None) -> "AwayPolicy":
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise ValueError("Invalid away rule")
        policy = cls(**{key: value[key] for key in cls.__dataclass_fields__ if key in value})
        if type(policy.enabled) is not bool:
            raise ValueError("Invalid away rule")
        if not policy.enabled:
            return policy
        if (not isinstance(policy.history_db, str) or not Path(policy.history_db).is_absolute()
                or not isinstance(policy.device_name, str) or not 1 <= len(policy.device_name) <= 128
                or not isinstance(policy.home_label, str) or not 1 <= len(policy.home_label) <= 128
                or policy.observer_at_home is not True):
            raise ValueError("Away rule requires an exact device and history from a stationary home Mac")
        limits = [(policy.origin_uncertainty_m, 100, 5000), (policy.radius_m, MILE_METERS, 100000),
                  (policy.max_scan_age, 15, 90), (policy.minimum_confidence, 0.9, 1),
                  (policy.minimum_open_seconds, 20, 300), (policy.home_radius_m, 100, 500)]
        if not all(finite_number(value) and low <= value <= high for value, low, high in limits):
            raise ValueError("Invalid away thresholds")
        return policy

    def eligible(self, observation: dict | None, now: float) -> bool:
        if (not self.enabled or not isinstance(observation, dict) or observation.get("state") != "away"
                or observation.get("device_name") != self.device_name or observation.get("source") != SOURCE):
            return False
        captured, lower = observation.get("observed_at"), observation.get("distance_lower_m")
        return (finite_number(captured) and 0 <= now - captured <= self.max_scan_age
                and finite_number(lower) and lower > self.radius_m)

    def confirmed_home(self, observation: dict | None, now: float) -> bool:
        if (not self.enabled or not isinstance(observation, dict) or observation.get("state") != "near"
                or observation.get("at_home") is not True or observation.get("source") != SOURCE
                or observation.get("device_name") != self.device_name):
            return False
        captured = observation.get("observed_at")
        return finite_number(captured) and 0 <= now - captured <= self.max_scan_age


def displayed_distance(text: str, uncertainty_m: float) -> tuple[float, float, float] | None:
    """Return displayed metres and a lower bound including rounding error."""
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(mi|km|ft|m)", text.strip(), re.I)
    if not match:
        return None
    number, unit = match.groups()
    value = float(number)
    if not math.isfinite(value) or value > 100000:
        return None
    decimal_places = len(number.partition(".")[2])
    resolution = 10 ** -decimal_places
    factor = {"mi": MILE_METERS, "km": 1000, "ft": .3048, "m": 1}[unit.lower()]
    return (value * factor, max(0, (value - resolution / 2) * factor - uncertainty_m),
            (value + resolution / 2) * factor + uncertainty_m)


class TagTrackerAwayService:
    def __init__(self, *, clock=None):
        self.clock = clock or time.time

    def observe(self, policy: AwayPolicy) -> dict:
        def unknown(reason):
            return {"state": "unknown", "reason": reason}
        if not policy.enabled:
            return unknown("disabled")
        try:
            path = Path(policy.history_db)
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
                db.row_factory = sqlite3.Row
                row = db.execute("SELECT ts,name,location,staleness,distance,raw_json FROM observations "
                                 "WHERE kind='devices' AND name=? ORDER BY id DESC LIMIT 1",
                                 (policy.device_name,)).fetchone()
            if row is None:
                return unknown("device_not_seen")
            now = self.clock()
            raw = json.loads(row["raw_json"])
            captured, finished = raw.get("captured_at"), raw.get("finished_at")
            if (raw.get("source") != SOURCE or raw.get("complete_scan") is not True
                    or raw.get("name") != policy.device_name or not raw.get("scan_id")
                    or not finite_number(captured) or not finite_number(finished)
                    or not 0 <= now - captured <= policy.max_scan_age
                    or not captured <= finished <= now):
                return unknown("scan_stale_or_unverified")
            stamp = datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))
            if stamp.tzinfo is None or abs(stamp.timestamp() - captured) > 1:
                return unknown("invalid_capture_time")
            status = json.loads(path.with_name("device-collection-status.json").read_text())
            if (status.get("ok") is not True or status.get("scan_id") != raw["scan_id"]
                    or status.get("checked_at") != finished):
                return unknown("device_scan_incomplete")
            if (not finite_number(raw.get("confidence")) or raw["confidence"] < policy.minimum_confidence
                    or raw.get("staleness") != row["staleness"] or row["staleness"].strip().casefold() != "now"
                    or raw.get("location") != row["location"] or not row["location"]
                    or any(word in row["location"].casefold() for word in ("unavailable", "offline", "not found", "no location"))):
                return unknown("findmy_location_not_current")
            if (raw.get("distance") != row["distance"] or not finite_number(raw.get("distance_confidence"))
                    or raw["distance_confidence"] < policy.minimum_confidence):
                return unknown("distance_unverified")
            confirmation = raw.get("confirmation_started_at")
            confirmed = (raw.get("confirmed_captures") == 2 and finite_number(confirmation)
                         and .5 <= captured - confirmation <= 30)
            home_label = row["location"].strip().casefold() == policy.home_label.strip().casefold()
            if row["distance"] == "Nearby":
                return {"state": "near", "reason": "findmy_nearby", "source": SOURCE,
                        "device_name": policy.device_name, "observed_at": captured, "scan_id": raw["scan_id"],
                        "at_home": confirmed and home_label}
            distance = displayed_distance(row["distance"], policy.origin_uncertainty_m)
            if distance is None:
                return unknown("distance_unavailable")
            metres, lower, upper = distance
            if lower > policy.radius_m and not confirmed:
                return unknown("distance_not_confirmed")
            return {"state": "away" if lower > policy.radius_m else "near", "reason": "findmy_displayed",
                    "source": SOURCE, "device_name": policy.device_name, "observed_at": captured,
                    "distance_m": round(metres, 2), "distance_lower_m": round(lower, 2), "scan_id": raw["scan_id"],
                    "at_home": confirmed and home_label and upper <= policy.home_radius_m}
        except (OSError, ValueError, TypeError, AttributeError, KeyError, sqlite3.Error):
            return unknown("history_unavailable")


class AwayTripGuard:
    """A durable relay-side trip lock shared by all bridge listeners."""
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def _locked(self):
        with self.path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = json.loads(self.path.read_text()) if self.path.exists() else {"used": False}
            if (not isinstance(state, dict) or type(state.get("used")) is not bool
                    or (state["used"] and not (finite_number(state.get("claimed_at")) and state["claimed_at"] >= 0))):
                raise ValueError("Invalid away trip guard; manual review required")
            yield state

    def _reset(self, state: dict, policy: AwayPolicy | None, observation: dict | None, now: float):
        if (state["used"] and policy and policy.confirmed_home(observation, now)
                and observation["observed_at"] > state["claimed_at"]):
            state.update(used=False, reset_at=observation["observed_at"])
            write_private_json(self.path, state, durable=True)

    def reset_home(self, policy: AwayPolicy, observation: dict | None, now: float):
        if not self.path.exists():
            return
        with self._locked() as state:
            self._reset(state, policy, observation, now)

    def allow(self, trigger: str, policy: AwayPolicy | None, observation: dict | None, now: float, key: str) -> bool:
        with self._locked() as state:
            self._reset(state, policy, observation, now)
            if state["used"]:
                return False
            if trigger == "device_away":
                state.update(used=True, claimed_at=now, idempotency_key=key)
                # A crash or a lost relay acknowledgement must never free this trip.
                write_private_json(self.path, state, durable=True)
            return True
