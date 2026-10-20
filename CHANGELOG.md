# Changelog

## Unreleased

- Add a repeatable, offscreen demo recorder for README screenshots, GIF and MP4,
  using the production views and fictional camera data. Extend the isolated demo
  bridge with simulated camera pictures and Open/Closed observations.

- Show the latest camera picture with its original capture time and an older-image
  label in the Mac and iPhone apps, plus a link to the configured camera's Ring
  Live View. Serve one private preview through authenticated, non-cacheable reads.

- Quit the Mac companion app when its last window closes, removing its Dock icon
  while the desktop widget and bridge services continue independently.

- Read existing Ring camera images without forced capture by default, preserving
  actual capture times and freshness checks; keep continuous forcing opt-in for
  verified powered cameras.
- Follow Ring's next-frame cursor for powered cameras so recent uploaded images
  can be used without waiting for an unnecessarily newer capture.

- Add an optional one-mile distance close rule using recent TagTracker Devices
  scans from a stationary home Mac, exact device identity, Find My freshness,
  conservative distance rounding and a durable one-attempt-per-trip lockout
  that resets only on a confirmed Home reading. Retire
  iPhone background location collection.
- Label app and widget actions Open Gate when the camera sees Closed, Close Gate
  when it sees Open, and Open / Close when the observation is unclear or expired.
  Match the app's confirmation dialog to the observed state, and use an open
  or closed gate icon for the action instead of bidirectional arrows.
- Add an opt-in overnight close rule after forty minutes of continuously observed
  Open status, with a fresh final camera check, shared command locking, a durable
  single-attempt journal and camera confirmation of closure. Uncertain results
  never cause an automatic retry.
- Add an optional continuous camera monitor with a persistent Ring connection,
  ten-second checks, shared observations, bounded logs and failure backoff.
  Refresh the foreground app every ten seconds and request widget updates every
  minute, subject to WidgetKit scheduling.
- Show optional Ring camera observations in both apps and widgets, with separate
  day/night references, observation times and a read-only refresh button. Stale,
  unavailable or unclear images never become a confident gate position.
- Polish the shared app and widget layouts with a prominent camera status,
  separate refresh control, clearer command feedback and simpler connection setup.

- Fix signed app and widget connection sharing with an explicit shared Keychain
  access group, and preserve settings from earlier local builds.
- Keep the last pulse result visible in the app and widget, including failures
  and interrupted requests. An accepted pulse never claims physical movement.
- Reject malformed success responses and unconfirmed controller acknowledgements;
  record acknowledgement return codes without recording credentials.
- Cover native client success, rejection and invalid responses using an isolated
  HTTP server on macOS. Tests never activate hardware.

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
