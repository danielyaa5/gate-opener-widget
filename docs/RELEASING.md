# Publishing a release

The public repository is built from the source export, never from the private development
history. Each release has two downloads: the notarized Mac app and the source ZIP.

1. Update `VERSION`, `CHANGELOG.md`, and `MARKETING_VERSION` (and bump
   `CURRENT_PROJECT_VERSION`) in both project specs.
2. Build the Mac download. This needs a paid Apple Developer team signed in to Xcode; signing
   and the notarization upload go through that account, so no password or key is handled here:

   ```sh
   TEAM_ID=YOURTEAMID BUNDLE_PREFIX=com.yourname macos/release.sh 1.0.0
   ```

   The result is `dist/mac/1.0.0/GateOpener-mac.zip`. Keep that file name: the README's
   download link points at `releases/latest/download/GateOpener-mac.zip`. The app contains no
   bridge URL or secret; each person pastes their own setup code.
3. Run `.venv/bin/python scripts/validate_release.py --apple-builds`. It exports the source,
   verifies checksums, runs the tests in a fresh extraction, confirms byte-identical repackaging,
   and builds unsigned Mac and iPhone apps. It doesn't touch the running bridge or open a gate.
   Inspect `dist/validation/report.json`; it records the archive's SHA-256 and lists what it
   didn't test.
4. Check the signed Mac app and its desktop widget against `scripts/demo_bridge.py`. Physical
   gate tests are done by a person at the gate; no script should actuate hardware.
5. Copy the exported files into the public repository, review the diff, and commit. Never copy
   `.env`, `LocalConfiguration.swift`, logs, Xcode archives or build products.
6. After CI passes, create the release tag matching `VERSION` and attach
   `GateOpener-mac.zip`, `dist/gate-opener-source.zip` and its `.zip.sha256`. Use the changelog
   entry as the release notes.

The website is `docs/index.html` with its images in `docs/images/`, served by GitHub Pages from
the `docs` folder. Demo images must come from `scripts/demo_bridge.py`, never a real bridge, and
must not show anything personal on the desktop.

The source ZIP is reproducible for identical source and Python/zlib implementation.
`SHA256SUMS` inside it covers every file except itself; the separate `.zip.sha256` covers the
whole archive. Regenerate validation after any change; an older report applies only to the
digest it records.
