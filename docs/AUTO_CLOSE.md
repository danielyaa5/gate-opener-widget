# Optional automatic closing

The always-on bridge can request a close after the camera has continuously
reported Open for at least forty minutes during a configured night window.
The rule is disabled unless explicitly configured and enabled.

An optional phone-distance rule can also request closure at any time of day.
Both triggers share one attempt journal, so an uncertain result never causes
the other rule to send a second pulse.

The relay is a single Open / Close toggle. The service therefore checks the
latest fresh camera observation again immediately before sending one pulse.
It never treats relay acceptance as proof that the gate moved or closed.

## Configuration

Keep the continuous camera monitor from [CAMERA.md](CAMERA.md) running. It tracks
`open_since` using actual capture timestamps. A closed, unclear, stale or missing
camera view resets continuity. An opening that began before the night window
still counts, provided observations remain continuous. Restart gaps longer than
thirty seconds reset the timer.

Create a private file outside the repository:

```json
{
  "enabled": true,
  "start_hour": 21,
  "end_hour": 6,
  "timezone": "UTC",
  "after_seconds": 2400,
  "max_image_age": 15,
  "minimum_match_score": 0.95
}
```

Choose the intended IANA timezone explicitly. `end_hour: 0` stops the rule at
midnight. Windows that cross midnight continue into the next morning. Duration
uses elapsed seconds, including across daylight saving time changes.

Set `GATE_AUTO_CLOSE_CONFIG` in the bridge's private `.env` to the file's absolute
path. Restart all bridge listeners to load the path. Then run:

```sh
.venv/bin/python -m gate_bridge.auto_close --config /path/to/private/auto-close.json --log-file /path/to/private/auto-close.log
```

Use `launchd/gate-auto-close.plist.template` for a user LaunchAgent on the always-on
computer. Replace the template paths before loading it. The rule rereads its
configuration every ten seconds; setting `enabled` to false stops future actions.

## Action and verification

### Close when a tracked device is away

Enable Devices collection in TagTracker, then add an `away` object to the
same private rule configuration:

```json
{
  "enabled": true,
  "history_db": "/path/to/private/TagTracker/history.sqlite",
  "device_name": "Example Phone",
  "observer_at_home": true,
  "radius_m": 1609.344,
  "origin_uncertainty_m": 200,
  "max_scan_age": 90,
  "minimum_confidence": 0.9,
  "minimum_open_seconds": 20
}
```

Use the exact device name displayed in Find My. The distance is the number
Find My displays relative to the scanning Mac. Enable this rule only on a Mac
that stays at home; disable it if that Mac moves. Actual names and history paths
belong in private configuration. No phone location permission or coordinate
upload endpoint is needed in Gate Opener.

TagTracker must have completed a Devices sweep, including the configured device,
within ninety seconds, with Find My explicitly reporting **Now**. An old, missing,
offline, ambiguous or poorly recognized location never qualifies. Each record
retains its original capture time and displayed age; a new scan is not a new GPS
fix. Failed scans invalidate earlier data. The rule reads SQLite in read-only mode.
It never uses town-center geocoding or a saved map pin to infer distance.

A numeric distance must agree in two separate captures within the scan.
The bridge subtracts half a displayed distance unit for rounding, plus the
configured home-Mac uncertainty, before comparing with one mile. For example,
`1 mi` is too imprecise to qualify. `Nearby` means near and never permits closing.
The camera must also show a strong Open match continuously for twenty seconds.
The bridge rechecks both sources under the shared command lock before a pulse.
This trigger has no night-window or forty-minute requirement.

Only one distance-based attempt is allowed per trip. Its lockout is saved before
transmission and survives a lost response, process restart, gate closure, gate
reopening, stale location data and crossing back inside one mile. The overnight
rule also respects this lockout. A fresh **Home** observation, confirmed in two
separate captures and captured after the attempt, resets the trip allowance.
It must show Nearby or a distance whose upper bound is within 400 metres of the
home Mac. A nearby place with a different label does not reset it.

A second durable `.state.trip.json` guard at the relay prevents another listener
or a newly started bridge from sending a second conditional close for that trip.
Manual app controls remain available. Returning home resets the trip allowance,
but does not erase an uncertain hardware outcome in the main attempt journal.

TagTracker normally scans about every five minutes while its Mac is idle and
Screen Sharing is disconnected. Location-based closure can therefore lag by
that interval plus Find My reporting delays. The gate daemon checks every ten
seconds, but expires a device reading after ninety seconds and waits for the
next successful scan. The private attempt journal includes the latest location
state and reason for inspection without exposing locations in diagnostic logs.

### Sending one close and confirming the result

The daemon sends an authenticated `POST /v1/close-if-open` to the local bridge.
The bridge independently enforces the enabled overnight or distance rule, duration, strong match,
snapshot age and matching open episode. Manual and automatic commands share a
file lock across bridge listeners. The Alexa key cannot access this endpoint.

The local match score measures reference similarity; it is not a statistical
probability. The automatic rule uses a stricter threshold than the status display.
No camera image or credential is written to its journal or logs.

Before transmission, the daemon durably records an attempt in a private state
file next to the rule. A crash, lost response or controller error cannot cause a
second pulse for that attempt. Three distinct, consecutive fresh Closed images
confirm closure and rearm the rule for a new opening. If closure is not confirmed
within two minutes, the journal reports `needs_attention` and no retry occurs.
An unknown view never rearms an attempted episode. Inspect the journal and
bounded log if intervention is needed; do not delete a pending journal to retry.

The gate controller remains responsible for obstruction sensing and movement.
All software validation uses a simulated controller; setup does not require
actuating the real gate merely to test the rule.
