# Changelog

## 1.0.0

First public release.

- Notarized Mac app download. The apps no longer compile in the bridge URL or API secret:
  each device is connected with a setup code from `scripts/setup_code.py`, stored in the
  Keychain and shared with the widget through the app group.
- Native macOS app and interactive desktop widget, independent of the iPhone.
- iPhone app, Home Screen widget, and optional iOS 18+ Lock Screen and Control Center control.
- Open / Close sends one momentary Tuya DP 1 pulse; movement depends on the controller.
- `scripts/demo_bridge.py`: a bridge with a simulated gate for trying the apps.
- Optional private Alexa skill with its own secret, idempotency keys and no automatic retries.
- Tailscale Funnel watchdog (`scripts/install_funnel_watchdog.py --public-url …`) and a
  LaunchDaemon option for a dedicated server Mac.
- HMAC authentication, timestamp and nonce checks, cooldown, and conservative retry behavior.
- Request-correlated client diagnostics with bounded storage.
- Website on GitHub Pages, reproducible source export with per-file and archive checksums.

### Known limitations

- No gate-position sensor: the apps can't tell whether the gate is open or closed.
- One shared API secret for all devices; there is no per-device revocation.
- The iPhone app is build-from-source only.
- Apple app builds are validated locally; hosted CI covers Python and packaging.
- The iPhone Lock Screen control intentionally works while the phone is locked.

## 1.0.0-rc.1

First source release candidate (not published).
