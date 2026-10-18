# Security

Do not put credentials or private endpoint URLs in a public issue. For a
security vulnerability, use the public repository's GitHub private vulnerability
reporting feature if enabled, or request a private contact without disclosing
exploit details. Maintainers should enable private reporting before publishing.

The bridge authenticates requests with HMAC, timestamps, and nonces. It keeps
the Tuya local key on the server. Remote access requires HTTPS; do not expose
the bridge port directly to the internet. The apps contain no secret; each device
stores the bridge URL and API secret from its setup code in the Keychain. Treat a
setup code like the secret itself. If it or `.env` is exposed, generate a new API
secret, restart the bridge, and paste a new setup code on every device.

The widget sends a momentary pulse. It does not know whether the gate is open
or closed, and cannot guarantee the direction of movement. Depending on the
controller, a pulse can open, close, or stop the gate. Retain the gate's physical
safety sensors and supervise hardware tests.

The iPhone Lock Screen control deliberately permits activation without unlocking
the phone. Remove that control if this behavior is unsuitable for your installation.

All devices share one API secret. There is no per-device credential revocation
in this version. The Alexa skill uses its own secret, `GATE_ALEXA_API_SECRET`.
