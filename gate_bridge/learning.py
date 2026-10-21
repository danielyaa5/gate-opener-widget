"""Owner-labeled camera examples. This module never contacts the gate controller."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import time
import uuid

from gate_bridge.position import (MAX_IMAGE_AGE, MAX_IMAGE_BYTES, classify_snapshot,
                                  lighting_mode, preview_path, read_movement, write_private_json)

PROMPT_COOLDOWN = 15 * 60
CANDIDATE_LIFETIME = 30 * 60
MAX_LEARNED_REFERENCES = 60


class GateLearningService:
    def __init__(self, config_path: Path | None, *, clock=time.time):
        self.config_path = config_path
        self.clock = clock

    @contextmanager
    def _locked(self):
        if self.config_path is None:
            raise ValueError("Camera learning is not configured")
        # Shared by both bridge processes. Lock separately from camera polling
        # and hardware commands so labeling cannot block a relay request.
        path = self.config_path.with_suffix(".learning.lock")
        with os.fdopen(os.open(path, os.O_RDWR | os.O_CREAT, 0o600), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.state_path = self.config_path.with_suffix(".learning.json")
            config = json.loads(self.config_path.read_text())
            try:
                state = json.loads(self.state_path.read_text())
            except FileNotFoundError:
                state = {"enabled": True}
            # A different camera or crop must never receive the old labels.
            identity = [str(config.get("camera_id", "")), config.get("roi", [0, 0, 1, 1])]
            if state.get("identity") != identity:
                state = {"enabled": state.get("enabled", True), "identity": identity}
            yield config, state

    def _reply(self, state):
        pending = state.get("pending")
        if pending and not 0 <= self.clock() - pending["created_at"] <= CANDIDATE_LIFETIME:
            state.pop("pending", None)
            pending = None
        return {"enabled": state.get("enabled") is True,
                "candidate": {k: pending[k] for k in ("id", "captured_at", "image_base64", "camera_name")} if pending else None}

    def candidate(self):
        if self.config_path is None:
            return {"enabled": False, "candidate": None}
        with self._locked() as (config, state):
            reply = self._reply(state)
            if reply["enabled"] and not reply["candidate"] and self.clock() - state.get("last_prompt_at", 0) >= PROMPT_COOLDOWN:
                try:
                    observation_path = Path(config["observation_file"])
                    observation = json.loads(observation_path.read_text())
                    preview = json.loads(preview_path(self.config_path, config).read_text())
                    captured = float(preview["captured_at"])
                    movement = read_movement(observation_path)
                    if (observation.get("state") == "unknown" and observation.get("reason") == "unclear"
                            and observation.get("observed_at") == captured
                            and preview.get("camera_id") == str(config.get("camera_id", ""))
                            and math.isfinite(captured) and -5 <= self.clock() - captured <= MAX_IMAGE_AGE
                            and self.clock() >= movement.get("moving_until", 0)
                            and captured >= movement.get("command_at", 0)):
                        data = base64.b64decode(preview["image_base64"], validate=True)
                        if len(data) <= MAX_IMAGE_BYTES and data.startswith(b"\xff\xd8") and lighting_mode(data) != "unusable":
                            digest = hashlib.sha256(data).hexdigest()
                            if digest not in state.get("resolved_images", []):
                                state["pending"] = {"id": uuid.uuid4().hex, "created_at": self.clock(),
                                                    "captured_at": captured, "image_base64": preview["image_base64"],
                                                    "camera_name": str(config.get("camera_name", "Gate camera"))[:80],
                                                    "digest": digest}
                                state["last_prompt_at"] = self.clock()
                except (OSError, ValueError, KeyError, TypeError):
                    pass
            reply = self._reply(state)
            write_private_json(self.state_path, state)
            return reply

    def answer(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("Invalid learning answer")
        with self._locked() as (config, state):
            if payload.get("action") == "set_enabled":
                if type(payload.get("enabled")) is not bool:
                    raise ValueError("Invalid learning setting")
                state["enabled"] = payload["enabled"]
                if not state["enabled"]:
                    state.pop("pending", None)
                write_private_json(self.state_path, state)
                return {"result": "enabled" if state["enabled"] else "disabled"}
            self._reply(state)
            for answered in state.get("answers", []):
                if payload.get("candidate_id") == answered["id"]:
                    if payload.get("state") != answered["state"]:
                        raise ValueError("This picture already has a different answer")
                    return {"result": answered["result"]}
            pending = state.get("pending")
            if not pending or payload.get("candidate_id") != pending["id"] or state.get("enabled") is not True:
                raise ValueError("This picture was already answered or expired. Refresh to continue.")
            label = payload.get("state")
            if label not in {"open", "closed", "skip"}:
                raise ValueError("Choose fully open, fully closed, or skip")
            result = "skipped"
            if label != "skip":
                data = base64.b64decode(pending["image_base64"], validate=True)
                references = config.get("references", [])
                if sum(r.get("learned") is True for r in references) >= MAX_LEARNED_REFERENCES:
                    raise ValueError("The learning library is full. Its examples need review before adding more.")
                folder = self.config_path.parent / "references" / "learned"
                folder.mkdir(mode=0o700, parents=True, exist_ok=True)
                path = folder / (pending["id"] + ".jpg")
                existing = next((r for r in references if r.get("path") == str(path)), None)
                if existing:
                    # Recover a completed config save if the response or state
                    # journal write was interrupted. Never append it twice.
                    if existing["state"] != label:
                        raise ValueError("This picture already has a different answer")
                else:
                    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
                        output.write(data)
                    updated = {**config, "references": references + [{"path": str(path), "state": label,
                               "lighting": lighting_mode(data), "learned": True, "captured_at": pending["captured_at"]}]}
                    try:
                        # Do not weaken the classifier or accept contradictory examples.
                        if classify_snapshot(data, updated)[0] != label:
                            raise ValueError("This picture conflicts with existing examples. Skip it if the gate is moving or partly open.")
                        # One rolling backup bounds storage and keeps prior references recoverable.
                        backup = self.config_path.with_suffix(".before-learning.json")
                        write_private_json(backup, config, durable=True)
                        write_private_json(self.config_path, updated, durable=True)
                    except Exception:
                        path.unlink(missing_ok=True)
                        raise
                result = "saved"
            state["resolved_images"] = (state.get("resolved_images", []) + [pending["digest"]])[-100:]
            state["last_prompt_at"] = self.clock()
            state["answers"] = (state.get("answers", []) + [{"id": pending["id"], "state": label, "result": result}])[-100:]
            state.pop("pending", None)
            write_private_json(self.state_path, state, durable=True)
            return {"result": result}
