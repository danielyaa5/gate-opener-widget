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

The widget sends a momentary pulse. An accepted command does not prove the gate
moved, and cannot guarantee its direction. Depending on the controller, a pulse
can open, close, or stop the gate. Optional Ring observations estimate Open,
Closed or Unclear by comparing snapshots with configured references. They can
be delayed or mistaken and do not replace the gate's physical safety sensors.
Automatic closing is disabled by default and requires fresh, strong Open
evidence, with durable attempt limits; see [AUTO_CLOSE.md](docs/AUTO_CLOSE.md).
Keep the physical safety sensors working and supervise hardware tests.

Ring credentials, calibration pictures, location history and automation journals
belong in private files outside this repository. The authenticated camera endpoint
serves the latest configured snapshot to paired clients; treat the shared API
secret as granting access to that picture as well as gate controls.

The iPhone Lock Screen control deliberately permits activation without unlocking
the phone. Remove that control if this behavior is unsuitable for your installation.

All devices share one API secret. There is no per-device credential revocation
in this version. The Alexa skill uses its own secret, `GATE_ALEXA_API_SECRET`.

## Optional camera dependency audit

The optional camera helper pins `ring-client-api` 11.8.1. Its full dependency tree
has known npm advisories; a clean bridge test run is not a clean dependency audit.
The snapshot helper imports only the library's REST client and logging utility.
An import trace on 2026-10-20 did not load the flagged streaming, socket, watcher,
or mock-server packages. The loaded `uuid` dependency has
[GHSA-w5hq-g745-h8pq](https://github.com/advisories/GHSA-w5hq-g745-h8pq), which affects
caller-supplied output buffers; the REST utility calls UUID v4/v5 without such
buffers. No affected call path was identified in this snapshot-only use.

This scope assessment does not fix the upstream dependency tree. Run `npm audit`
in `camera/` before deploying it, and reassess if imports or library versions
change. Do not reuse the pinned library for streaming or other Ring features
without a fresh review. The optional camera helper is separate from the Python
bridge and is not required for manual gate controls.
