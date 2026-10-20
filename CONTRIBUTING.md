# Contributing

Use Python 3.10+ for the bridge and Xcode 27.1+ with XcodeGen for the apps.
Start with the technical setup in [docs/SETUP.md](docs/SETUP.md).

Run `.venv/bin/python -m unittest discover -s tests -v` before proposing changes.
Tests use simulated controllers; never point automated tests at a real gate.
For app changes, regenerate the project from its `project.yml`, build both
platforms, and inspect the affected app or widget on a supported device.
Report which UI paths were tested and any hardware checks still outstanding.

Keep changes focused. Preserve the single-pulse behavior, cooldown, nonce
checking, and conservative retry policy. A timeout can occur after a command
was accepted; do not automatically resend an ambiguous gate activation.

Never include `.env`, setup codes, local keys, API secrets, device identifiers,
signing team IDs, or private endpoints in contributions. Use synthetic credentials
and example addresses in bug reports and tests. To try the apps without a gate,
run `.venv/bin/python scripts/demo_bridge.py` and paste the setup code it prints.

The app icon artwork is included under the repository license. Its master image
is `assets/gate-icon-master.png`.
Regenerate the checked-in assets on macOS with
`swift scripts/generate_icons.swift` from the repository root.

Build release source with `python3 scripts/package_source.py`. Inspect and test
the extracted ZIP before publishing it. The source exporter intentionally uses
the working tree, including uncommitted changes, and excludes repository history.
