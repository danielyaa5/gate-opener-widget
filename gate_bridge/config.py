from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path = PROJECT_ROOT / ".env") -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


@dataclass(frozen=True)
class Config:
    device_id: str
    local_key: str
    address: str
    protocol_version: float
    api_secret: str
    host: str = "0.0.0.0"
    port: int = 8765
    pulse_seconds: float = 0.5
    cooldown_seconds: float = 3.0
    alexa_api_secret: str | None = None
    camera_config: Path | None = None
    auto_close_config: Path | None = None

    @classmethod
    def from_env(cls, path: Path = PROJECT_ROOT / ".env") -> "Config":
        values = load_env(path)
        required = {
            "GATE_DEVICE_ID",
            "GATE_LOCAL_KEY",
            "GATE_ADDRESS",
            "GATE_PROTOCOL_VERSION",
            "GATE_API_SECRET",
        }
        missing = sorted(key for key in required if not values.get(key))
        if missing:
            raise ValueError(f"Missing configuration: {', '.join(missing)}")

        secret = values["GATE_API_SECRET"]
        if len(secret) < 32:
            raise ValueError("GATE_API_SECRET must contain at least 32 characters")

        alexa_secret = values.get("GATE_ALEXA_API_SECRET") or None
        if alexa_secret is not None:
            if len(alexa_secret) < 32:
                raise ValueError(
                    "GATE_ALEXA_API_SECRET must contain at least 32 characters"
                )
            if alexa_secret == secret:
                raise ValueError(
                    "GATE_ALEXA_API_SECRET must differ from GATE_API_SECRET"
                )

        return cls(
            device_id=values["GATE_DEVICE_ID"],
            local_key=values["GATE_LOCAL_KEY"],
            address=values["GATE_ADDRESS"],
            protocol_version=float(values["GATE_PROTOCOL_VERSION"]),
            api_secret=secret,
            host=values.get("GATE_BRIDGE_HOST", "0.0.0.0"),
            port=int(values.get("GATE_BRIDGE_PORT", "8765")),
            pulse_seconds=float(values.get("GATE_PULSE_SECONDS", "0.5")),
            cooldown_seconds=float(values.get("GATE_COOLDOWN_SECONDS", "3")),
            alexa_api_secret=alexa_secret,
            camera_config=Path(values["GATE_CAMERA_CONFIG"]).expanduser() if values.get("GATE_CAMERA_CONFIG") else None,
            auto_close_config=Path(values["GATE_AUTO_CLOSE_CONFIG"]).expanduser() if values.get("GATE_AUTO_CLOSE_CONFIG") else None,
        )
