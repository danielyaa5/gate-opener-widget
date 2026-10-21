# Gate position from Ring

The apps and widgets can show an optional camera observation independently of
the controller connection and the last relay pulse. The refresh button only
checks the camera. It never moves the gate.

Images are compared locally on the bridge Mac against labeled examples from
the same fixed camera. Keep separate open and closed examples for daylight and
infrared night vision. An unfamiliar, obstructed or ambiguous image returns
`unknown`; missing daylight examples never fall back to night examples. The
comparison is suitable for a fixed camera view, not a general object detector.

## Bridge setup

Use Node.js 20 for the pinned Ring client and install the optional dependencies:

```sh
.venv/bin/python -m pip install -r requirements-camera.txt
npm --prefix camera ci --ignore-scripts
```

Keep the Ring login token and all real reference images in a private directory
outside the checkout. The token file has the same `refreshToken` field used by
`ring-client-api`; do not place a Ring password in any configuration file.
Restrict the directory to its owner and token/image files to mode `600`.

Create a private JSON configuration, for example:

```json
{
  "camera_id": "123456789",
  "token_file": "/path/to/private/ring-config.json",
  "node": "/path/to/node20/bin/node",
  "roi": [0.0, 0.0, 1.0, 1.0],
  "references": [
    {"state": "open", "lighting": "day", "path": "/path/to/private/open-day.jpg"},
    {"state": "closed", "lighting": "day", "path": "/path/to/private/closed-day.jpg"},
    {"state": "open", "lighting": "night", "path": "/path/to/private/open-night.jpg"},
    {"state": "closed", "lighting": "night", "path": "/path/to/private/closed-night.jpg"}
  ]
}
```

`roi` is the image region containing the gate, as normalized left, top, right
and bottom coordinates. Exclude timestamps, watermarks and unrelated movement.
Confirm the real gate position for every example and validate additional
images that were not used as references. Add more examples if weather or
lighting changes; do not lower the thresholds to force a result.

For a fixed view where moving shadows repeatedly hide an otherwise clear
Closed match, the private configuration can opt into
`"shadow_tolerant_closed": true`. If the original comparison favors Closed but
rejects its exact pixel match, a second comparison pools edge directions in
small spatial cells. The original comparison must still pass its ambiguity
check and absolute distance limit. Both comparisons must favor Closed, and the second must
pass the same separation, distance and ambiguity checks. Validate independent
photos before enabling it. This recovery can only report Closed; it cannot
create an Open observation or make a new observation eligible for automatic
closing. Stale, missing and unusable images still return unknown.

The lighting names describe image color: `day` is color and `night` is
monochrome infrared. A camera that stays in color after dark needs its color
nighttime examples in the `day` group, alongside daylight examples.

When changing cameras, use references from the new camera's own view. Pause
automatic closing, stop the old monitor, and replace the camera configuration
and old observation before starting the new monitor. Preserve the automatic
close and trip-attempt journals. Check independent fresh open and closed images
through the bridge before restoring automatic closing.

Set `GATE_CAMERA_CONFIG` in the bridge's private `.env` to that JSON file's
absolute path and restart the bridge. Without it, existing control features
continue to work and camera status says it is not set up.

## Continuous checks for a powered camera

For frequent updates, add two fields to the private camera configuration:

```json
{
  "observation_file": "/path/to/private/observation.json",
  "poll_interval_seconds": 10
}
```

Keep the other fields and references from the example above. Run the monitor on
the always-on bridge computer:

```sh
.venv/bin/python -m gate_bridge.camera_monitor --config /path/to/private/camera.json --log-file /path/to/private/camera.log
```

Use `launchd/gate-camera.plist.template` to keep it running after login. Replace
the template's paths before installing it as a user LaunchAgent. Start only one
monitor for each configuration. A file lock prevents duplicate workers.

The monitor keeps one Ring client alive and checks the latest uploaded image
every ten seconds without waking the camera. A photo that is still old is not
relabeled as a fresh capture. Slow camera responses can lengthen that interval.
Ring errors use
exponential backoff and honor its `Retry-After` response. The latest image is
kept in one private preview file for the foreground apps; older frames are replaced.
Only the latest position and timestamps are written atomically to the private
observation file. Logs rotate and never contain images or credentials.

When `observation_file` is set, bridge requests read that file immediately and
never create additional Ring requests. Multiple bridge listeners therefore
share the same observation. Missing, corrupt or old results return unknown.
Commands invalidate observations across listeners; frames captured before the
command cannot restore the old position.

Empty cached-image responses retry at the normal polling interval and still
honor Ring's Retry-After. Other failures retain bounded exponential backoff.
If Ring's next-image request returns 404 without a Retry-After, the helper
also checks the latest uploaded image without forcing a second capture. It
accepts that fallback only when its capture timestamp is newer than the last
frame; the usual 30-second freshness check still applies. Request diagnostics
record only allowlisted error codes and HTTP status, never response contents.

## Learning from unclear pictures on the Mac

The Mac app's **Learning mode** asks you to label a fresh unclear picture as
**Fully Open**, **Fully Closed**, or **Skip / Not sure**. It keeps the exact picture
and capture time shown in the question, even if the camera has since updated.
Partly open, moving, obstructed, or uncertain views should be skipped.

Learning runs while the Mac app is open, including quietly after its main
window is closed. Its Dock icon disappears when all windows are closed and
returns for a learning question. Quitting the app stops questions. The iPhone
and widgets do not show learning prompts, but use the shared improved comparison.
The toggle in the Mac app turns learning off for the bridge. At most one question
is created per 15 minutes, identical answered images are suppressed, and an
unanswered picture expires after 30 minutes. Network errors, old pictures, and
possible movement do not create questions.

The app uses signed `GET /v1/learning` and `POST /v1/learning` requests. The Alexa
key cannot access them. Labels save private mode-600 reference files beside the
camera configuration, with an atomic configuration update and rolling backup.
The monitor reads new references on its next image; no restart is required.
Contradictory examples are rejected without relaxing recognition thresholds.
The library accepts up to 60 learned examples before requiring review. A label
never updates a past observation or sends a gate command. Newly matched fresh
observations remain subject to the existing auto-close rules and their guards.

## Camera feeds and freshness

`GET /v1/position` uses the app's existing HMAC authentication and returns only
the observation. The Alexa key does not grant access to camera routes.

The foreground apps also use `GET /v1/camera` to display the latest picture and
open the configured camera's Ring Live View. Set `camera_name` in the private
camera configuration to label the picture. This route requires the same app
authentication, excludes the Alexa key, and sends `Cache-Control: no-store`.
The monitor atomically replaces one mode-600 `.snapshot.json` file alongside
the observation file; it contains the image, camera identity and original
capture time, with no image history. Changing the configured camera rejects
the previous camera's picture. During camera errors, the last picture can be served
for up to 24 hours, explicitly labeled with its age; an image over 30 seconds old
is marked older. This preview never substitutes for a fresh position observation.
The apps hold the image in foreground memory only and do not put it in widget
settings, diagnostics or photo libraries. Backgrounding clears that image.
Camera credentials are never returned to the apps. Real images and credentials
are never included in source exports. Preview reads do not request extra Ring
captures or contact the gate controller.

By default the bridge reads Ring's existing cached image without forcing a new
capture. It checks Ring's capture timestamp and rejects images older than 30 seconds.
For a verified continuously powered camera, an explicit private `"force_snapshots": true`
requests an image newer than the previous frame, with a ten-second lookback on
the first request or after a long gap. Capture timestamps and the 30-second
freshness limit still apply. Battery doorbells
can still run from their battery when wired: the wire can provide only a trickle
charge. Do not enable continuous forced captures on such a camera.
Concurrent widget checks share a result for up to ten seconds. A camera timeout
or stale image returns `unknown`, including while a battery camera cannot
provide a snapshot during a recording. Camera requests do not contact the Tuya
controller and cannot delay relay commands.

A pulse invalidates the prior observation and reports possible movement for
20 seconds. It does not imply open or closed. Widget observations expire after
five minutes and show their original observation time. The foreground app checks
the camera every ten seconds without polling the gate controller. It pauses
when inactive and updates widget data when the position changes or once per minute.
Widgets request another timeline after one minute, but WidgetKit controls the
actual schedule and can delay or throttle updates. The refresh button requests
the latest available observation without moving the gate.

Keep the camera powered and its view fixed. This visual status is advisory;
gate motion, obstruction protection and access control remain the hardware
controller's responsibility.
