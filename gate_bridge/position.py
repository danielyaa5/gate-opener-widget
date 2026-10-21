"""Read-only gate observations from fresh camera images and local references."""
from __future__ import annotations

import base64
from dataclasses import dataclass
import io
import json
import math
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid
from typing import Callable


ROOT = Path(__file__).resolve().parent.parent
MAX_IMAGE_BYTES = 5_000_000
MAX_IMAGE_AGE = 30.0
RESULT_CACHE_SECONDS = 10.0
MAX_PREVIEW_AGE = 24 * 60 * 60


def preview_path(config_path: Path, configuration: dict) -> Path:
    observation = Path(configuration.get("observation_file") or config_path)
    return observation.with_suffix(".snapshot.json")


def save_preview(snapshot: CameraSnapshot, config_path: Path, configuration: dict, now: float) -> None:
    """Replace one private preview, keeping capture time and camera identity together."""
    age = now - snapshot.captured_at
    if not math.isfinite(age) or not -5 <= age <= MAX_PREVIEW_AGE:
        return
    try:
        write_private_json(preview_path(config_path, configuration), {
            "camera_id": str(configuration.get("camera_id", "")),
            "captured_at": snapshot.captured_at,
            "image_base64": base64.b64encode(snapshot.image).decode("ascii"),
        })
    except OSError:
        # A preview storage failure must not affect observations or auto-close.
        pass


def write_private_json(path: Path, value: dict, *, durable: bool = False) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as output:
            json.dump(value, output, allow_nan=False)
            if durable:
                output.flush()
                os.fsync(output.fileno())
        temporary.replace(path)
        if durable:
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def movement_path(observation_file: Path) -> Path:
    return observation_file.with_suffix(".movement.json")


def read_movement(observation_file: Path) -> dict:
    try:
        value = json.loads(movement_path(observation_file).read_text())
        if not isinstance(value, dict) or not all(
            isinstance(value.get(key), (int, float)) and math.isfinite(value[key])
            for key in ("command_at", "moving_until")
        ):
            raise ValueError("Invalid movement marker")
        return value
    except FileNotFoundError:
        return {}


@dataclass(frozen=True)
class CameraSnapshot:
    image: bytes
    captured_at: float


def lighting_mode(image: bytes) -> str:
    """Ring night vision is monochrome; never reuse that calibration for color."""
    from PIL import Image, ImageStat

    with Image.open(io.BytesIO(image)) as source:
        rgb = source.convert("RGB").resize((32, 32))
        stats = ImageStat.Stat(rgb)
        if max(stats.stddev) < 4:
            return "unusable"
        chroma = sum(max(pixel) - min(pixel) for pixel in rgb.getdata()) / (32 * 32)
        return "night" if chroma < 3 else "day"


def image_features(image: bytes, roi: list[float], *, pooled_edges: bool = False) -> list[float]:
    """Compare gate geometry with brightness-normalized edges and structure."""
    from PIL import Image, ImageFilter, ImageOps, ImageStat

    with Image.open(io.BytesIO(image)) as source:
        if source.width < 64 or source.height < 64 or source.width * source.height > 30_000_000:
            raise ValueError("Invalid camera image dimensions")
        box = tuple(round(value * (source.width if i % 2 == 0 else source.height)) for i, value in enumerate(roi))
        gray = ImageOps.autocontrast(source.crop(box).convert("L"), cutoff=1)
        if ImageStat.Stat(gray).stddev[0] < 5:
            raise ValueError("Camera view has too little detail")
        gray = gray.resize((64, 64)).filter(ImageFilter.GaussianBlur(0.7))
        pixels = list(gray.getdata())
    mean = sum(pixels) / len(pixels)
    structure = [pixel - mean for pixel in pixels]
    gradients = []
    for y in range(1, 63):
        for x in range(1, 63):
            index = y * 64 + x
            gradients.extend((pixels[index + 1] - pixels[index - 1], pixels[index + 64] - pixels[index - 64]))
    def unit(values: list[float]) -> list[float]:
        norm = math.sqrt(sum(value * value for value in values))
        if norm < 1:
            raise ValueError("Camera view has too little detail")
        return [value / norm for value in values]
    if pooled_edges:
        # Pool edge directions within 8x8 cells so a moving shadow or small
        # exposure change need not match the exact reference pixels. Retain
        # spatial layout: one histogram for the whole crop loses gate geometry.
        bins = 8
        histogram = [0.0] * (8 * 8 * bins)
        for y in range(1, 63):
            for x in range(1, 63):
                index = y * 64 + x
                dx = pixels[index + 1] - pixels[index - 1]
                dy = pixels[index + 64] - pixels[index - 64]
                direction = (math.atan2(dy, dx) % math.pi) / math.pi * bins
                start = ((y // 8) * 8 + x // 8) * bins
                magnitude = math.hypot(dx, dy)
                histogram[start + int(direction) % bins] += magnitude * (1 - direction % 1)
                histogram[start + (int(direction) + 1) % bins] += magnitude * (direction % 1)
        return unit(histogram)
    return [value * math.sqrt(0.75) for value in unit(gradients)] + [value * 0.5 for value in unit(structure)]


def feature_distance(left: list[float], right: list[float]) -> float:
    return max(0.0, 1.0 - sum(a * b for a, b in zip(left, right)))


def classify_snapshot(image: bytes, configuration: dict) -> tuple[str, str, float | None]:
    mode = lighting_mode(image)
    if mode == "unusable":
        return "unknown", "unclear", None
    roi = configuration.get("roi", [0, 0, 1, 1])
    if (not isinstance(roi, list) or len(roi) != 4 or
            not all(isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= 1 for value in roi) or
            roi[0] >= roi[2] or roi[1] >= roi[3]):
        raise ValueError("Invalid camera region")
    reference_images: dict[str, list[bytes]] = {"open": [], "closed": []}
    for reference in configuration.get("references", []):
        if reference.get("lighting") == mode and reference.get("state") in reference_images:
            data = Path(reference["path"]).read_bytes()
            if lighting_mode(data) != mode:
                continue
            reference_images[reference["state"]].append(data)
    if not all(reference_images.values()):
        return "unknown", "needs_day_references" if mode == "day" else "needs_night_references", None
    return _match_snapshot(image, reference_images, roi,
                           shadow_tolerant_closed=configuration.get("shadow_tolerant_closed") is True)


def _match_snapshot(image: bytes, reference_images: dict[str, list[bytes]], roi: list[float],
                    *, pooled_edges: bool = False,
                    shadow_tolerant_closed: bool = False) -> tuple[str, str, float | None]:
    references = {state: [image_features(data, roi, pooled_edges=pooled_edges) for data in images]
                  for state, images in reference_images.items()}
    features = image_features(image, roi, pooled_edges=pooled_edges)
    distances = {state: min(feature_distance(features, ref) for ref in refs) for state, refs in references.items()}
    separation = min(feature_distance(a, b) for a in references["open"] for b in references["closed"])
    state = min(distances, key=distances.get)
    best = distances[state]
    other = distances["closed" if state == "open" else "open"]
    # Reject near-identical references, unfamiliar/obstructed views and ambiguous
    # intermediate positions instead of converting "different" into "open".
    if separation < 0.18 or best > min(0.25, separation * 0.22) or other - best < max(0.08, separation * 0.5):
        if (shadow_tolerant_closed and state == "closed" and separation >= 0.18 and best <= 0.25
                and other - best >= max(0.08, separation * 0.5)):
            # Recover only Closed, and only when both comparisons favor it.
            # Open observations eligible for auto-close still require the
            # original matcher. Never reuse an earlier observation here.
            pooled = _match_snapshot(image, reference_images, roi, pooled_edges=True)
            if pooled[0] == "closed":
                return pooled
        return "unknown", "unclear", None
    return state, "matched", round(max(0.0, min(1.0, 1 - best / separation)), 3)


def snapshot_observation(snapshot: CameraSnapshot, configuration: dict, now: float) -> dict:
    age = now - snapshot.captured_at
    if not math.isfinite(age) or not -5 <= age <= MAX_IMAGE_AGE:
        return {"state": "unknown", "observed_at": snapshot.captured_at if math.isfinite(snapshot.captured_at) else None,
                "checked_at": now, "reason": "stale_snapshot", "match_score": None}
    state, reason, score = classify_snapshot(snapshot.image, configuration)
    return {"state": state, "observed_at": snapshot.captured_at, "checked_at": now, "reason": reason, "match_score": score}


class GatePositionService:
    def __init__(self, config_path: Path | None, *, snapshot_loader: Callable[[dict], CameraSnapshot] | None = None, clock: Callable[[], float] = time.time) -> None:
        self.config_path = config_path
        self.snapshot_loader = snapshot_loader or self._load_snapshot
        self.clock = clock
        self._lock = threading.Lock()
        self._cached: dict | None = None
        self._moving_until = 0.0
        self._generation = 0

    def _unknown(self, reason: str, observed_at: float | None = None) -> dict:
        return {"state": "unknown", "observed_at": observed_at, "checked_at": self.clock(), "reason": reason, "match_score": None}

    def camera_preview(self) -> dict:
        """Read the last picture without requesting a capture or touching the gate."""
        result = {"camera_name": "Gate camera", "ring_url": "https://account.ring.com/account/dashboard",
                  "captured_at": None, "image_base64": None}
        if self.config_path is None:
            return result
        try:
            config = json.loads(self.config_path.read_text())
            if not isinstance(config, dict):
                return result
            camera_id = str(config.get("camera_id", ""))
            result["camera_name"] = str(config.get("camera_name") or "Gate camera")[:80]
            if camera_id.isascii() and camera_id.isdecimal():
                result["ring_url"] += "?lv_d=" + camera_id
            with preview_path(self.config_path, config).open("rb") as source:
                raw = source.read(MAX_IMAGE_BYTES * 2 + 1)
            if len(raw) > MAX_IMAGE_BYTES * 2:
                return result
            saved = json.loads(raw)
            captured = float(saved["captured_at"])
            if saved["camera_id"] != camera_id or not math.isfinite(captured) or not -5 <= self.clock() - captured <= MAX_PREVIEW_AGE:
                return result
            data = base64.b64decode(saved["image_base64"], validate=True)
            if not data.startswith(b"\xff\xd8") or len(data) > MAX_IMAGE_BYTES:
                return result
            result.update(captured_at=captured, image_base64=saved["image_base64"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return result

    def invalidate(self) -> None:
        # A slow camera request must never delay a gate command. An observation
        # already in flight is discarded if a pulse changes this generation.
        self._generation += 1
        self._moving_until = self.clock() + 20
        self._cached = None
        if self.config_path:
            try:
                config = json.loads(self.config_path.read_text())
                if config.get("observation_file"):
                    write_private_json(movement_path(Path(config["observation_file"])),
                                       {"command_at": self.clock(), "moving_until": self._moving_until})
            except (OSError, ValueError, KeyError, TypeError):
                # A camera storage failure must not prevent the relay command.
                pass

    def _read_monitored_observation(self, path: Path) -> dict:
        now = self.clock()
        movement = read_movement(path)
        if now < movement.get("moving_until", 0):
            return self._unknown("movement")
        result = json.loads(path.read_text())
        checked_at = float(result["checked_at"])
        if not math.isfinite(checked_at) or not -5 <= now - checked_at <= MAX_IMAGE_AGE:
            return self._unknown("camera_unavailable")
        if checked_at < movement.get("command_at", 0):
            return self._unknown("movement")
        if result.get("state") in {"open", "closed"}:
            captured = float(result["observed_at"])
            if not math.isfinite(captured) or not -5 <= now - captured <= MAX_IMAGE_AGE:
                return self._unknown("stale_snapshot")
            if captured < movement.get("command_at", 0):
                return self._unknown("movement")
            if result.get("reason") != "matched":
                return self._unknown("unclear")
        elif result.get("state") != "unknown":
            return self._unknown("unclear")
        return result

    def observe(self) -> dict:
        if self.config_path is None:
            return self._unknown("not_configured")
        with self._lock:
            now = self.clock()
            if now < self._moving_until:
                return self._unknown("movement")
            generation = self._generation
            try:
                configuration = json.loads(self.config_path.read_text())
                if configuration.get("observation_file"):
                    return self._read_monitored_observation(Path(configuration["observation_file"]))
                if self._cached and 0 <= now - self._cached["checked_at"] < RESULT_CACHE_SECONDS:
                    return dict(self._cached)
                snapshot = self.snapshot_loader(configuration)
                result = snapshot_observation(snapshot, configuration, self.clock())
                save_preview(snapshot, self.config_path, configuration, self.clock())
            except (OSError, ValueError, KeyError, TypeError, ImportError, subprocess.SubprocessError):
                result = self._unknown("camera_unavailable")
            if generation != self._generation:
                return self._unknown("movement")
            self._cached = result
            return dict(result)

    def _load_snapshot(self, configuration: dict) -> CameraSnapshot:
        completed = subprocess.run(
            [configuration.get("node", "node"), str(ROOT / "camera" / "snapshot.cjs"), str(self.config_path)],
            capture_output=True, timeout=25, check=True,
        )
        if len(completed.stdout) > MAX_IMAGE_BYTES * 1.4:
            raise ValueError("Camera image is too large")
        payload = json.loads(completed.stdout)
        image = base64.b64decode(payload["image"], validate=True)
        if len(image) > MAX_IMAGE_BYTES or not image.startswith(b"\xff\xd8"):
            raise ValueError("Invalid camera image")
        return CameraSnapshot(image=image, captured_at=float(payload["captured_at"]))
