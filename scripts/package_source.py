#!/usr/bin/env python3
"""Export reviewable source, never local configuration or repository history."""
from __future__ import annotations

import re
import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parent.parent
PATTERNS = (
    "gate_bridge/*.py", "alexa_skill/*.py", "alexa_skill/*.json", "tests/*.py", "scripts/*.py", "scripts/*.swift", "launchd/*.template",
    "assets/**/*.json", "assets/**/*.png", ".github/workflows/*.yml",
    ".github/ISSUE_TEMPLATE/*.yml", ".github/PULL_REQUEST_TEMPLATE.md", "docs/*.md", "docs/*.html",
    "docs/images/*.png", "docs/images/*.jpg", "docs/images/*.jpeg", "docs/images/*.gif", "docs/images/*.mp4", "docs/images/*.json", "docs/images/*.svg",
    "ios/GateOpener/App/*.swift", "ios/GateOpener/Shared/*.swift",
"ios/GateOpenerWidget/*.swift",
    "ios/GateOpenerWidget/Info.plist", "ios/project.yml", "macos/project.yml",
    "macos/GateOpenerMac/*.swift", "macos/GateOpenerMacWidget/*.swift",
    "macos/GateOpenerMacWidget/Info.plist",
    "camera/*.cjs", "camera/package.json", "camera/package-lock.json",
)
EXCLUDED = "ios/GateOpener/Shared/LocalConfiguration.swift"
PROJECT_SPECS = ("ios/project.yml", "macos/project.yml")
SIGNING_FILES = PROJECT_SPECS + ("ios/GateOpener.xcodeproj/project.pbxproj", "macos/GateOpenerMac.xcodeproj/project.pbxproj")
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".mp4"}
PUBLIC_PREFIX = "org.example"
PUBLIC_APP_ID = "org.example.GateOpener"


def sanitize_project_spec(contents: str) -> str:
    """Replace whatever signing team and bundle identifiers are configured with placeholders."""
    contents = re.sub(r"(?m)^[ \t]*DEVELOPMENT_TEAM:.*\n", "", contents)
    contents = re.sub(r"(?m)^([ \t]*(?:bundleIdPrefix|GATE_BUNDLE_PREFIX):[ \t]*).*$", rf"\g<1>{PUBLIC_PREFIX}", contents)
    pattern = r"(?m)^([ \t]*PRODUCT_BUNDLE_IDENTIFIER:[ \t]*)(?!\$)(\S+)[ \t]*$"
    identifiers = re.findall(pattern, contents)
    if identifiers:
        base = min((value for _, value in identifiers), key=len)
        if not all(value.startswith(base) for _, value in identifiers):
            raise ValueError("Bundle identifiers do not share an app prefix; export refused")
        contents = re.sub(pattern, lambda match: match.group(1) + PUBLIC_APP_ID + match.group(2)[len(base):], contents)
    return contents


def personal_signing_values() -> set[str]:
    """Signing team IDs configured locally, none of which may appear anywhere in the export.

    Bundle prefixes are replaced in the project specs but not refused elsewhere, because the
    reverse-DNS namespace also names launchd jobs and log subsystems in the public source.
    """
    values = set()
    for name in SIGNING_FILES:
        path = ROOT / name
        if path.exists():
            values.update(re.findall(r"DEVELOPMENT_TEAM\s*[:=]\s*\"?([A-Z0-9]{10})\b", path.read_text()))
    return values


def export_files() -> dict[str, str | bytes]:
    # The private checkout publishes RELEASE.md as the README and keeps its own README.md out
    # of the export; an exported copy, such as the public repository, has only README.md.
    readme = "RELEASE.md" if (ROOT / "RELEASE.md").exists() else "README.md"
    paths = {ROOT / name for name in ("LICENSE", "VERSION", "CHANGELOG.md", readme, "CONTRIBUTING.md", "SECURITY.md", "requirements.txt", "requirements-camera.txt", ".gitignore", ".env.example")}
    for pattern in PATTERNS:
        paths.update(ROOT.glob(pattern))
    files = {}
    for path in sorted(paths):
        name = path.relative_to(ROOT).as_posix()
        if name == EXCLUDED:
            continue
        if path.is_symlink():
            raise ValueError(f"Refusing symlink: {name}")
        if path.suffix in BINARY_SUFFIXES:
            files[name] = path.read_bytes()
            continue
        contents = path.read_text()
        if name in PROJECT_SPECS:
            contents = sanitize_project_spec(contents)
        if name == ".env.example":
            contents = re.sub(r"(?m)^GATE_ADDRESS=.*$", "GATE_ADDRESS=192.168.1.100", contents)
        files[name] = contents
    files["README.md"] = files.pop(readme)
    return files


def write_archive(files: dict[str, str | bytes], destination: Path) -> None:
    payloads = {name: value.encode() if isinstance(value, str) else value for name, value in files.items()}
    manifest = "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in sorted(payloads.items()))
    payloads["SHA256SUMS"] = manifest.encode()
    with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
        for name, data in sorted(payloads.items()):
            info = ZipInfo(f"gate-opener/{name}", date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)


def check_local_values(files: dict[str, str | bytes]) -> None:
    values = set()
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() in {"GATE_DEVICE_ID", "GATE_LOCAL_KEY", "GATE_API_SECRET"} and value.strip():
                values.add(value.strip())
    config = ROOT / EXCLUDED
    if config.exists():
        values.update(re.findall(r'apiSecret\s*=\s*"([^"\n]+)"', config.read_text()))
        values.update(re.findall(r'URL\(string:\s*"([^"\n]+)"', config.read_text()))
    values.update(personal_signing_values())
    for name, contents in files.items():
        data = contents.encode() if isinstance(contents, str) else contents
        if any(value.encode() in data for value in values):
            raise ValueError(f"Local credential, endpoint or signing identity found in {name}; export refused")


def main() -> None:
    files = export_files()
    check_local_values(files)
    destination = ROOT / "dist" / "gate-opener-source.zip"
    destination.parent.mkdir(exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    try:
        write_archive(files, temporary)
        temporary.replace(destination)
        destination.with_suffix('.zip.sha256').write_text(
            f"{hashlib.sha256(destination.read_bytes()).hexdigest()}  {destination.name}\n"
        )
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Exported {len(files)} source files to {destination}")


if __name__ == "__main__":
    main()
