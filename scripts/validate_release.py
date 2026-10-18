#!/usr/bin/env python3
"""Validate an extracted source release without using credentials or real hardware."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apple-builds", action="store_true", help="Also build unsigned Mac and iPhone apps with Xcode")
    args = parser.parse_args()
    subprocess.run([sys.executable, "scripts/package_source.py"], cwd=ROOT, check=True)
    archive_path = ROOT / "dist/gate-opener-source.zip"
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    if archive_path.with_suffix(".zip.sha256").read_text().split()[0] != digest:
        raise ValueError("Archive checksum mismatch")
    results = {"version": (ROOT / "VERSION").read_text().strip(), "sha256": digest,
               "python": sys.version.split()[0], "checks": [],
               "not_tested": ["physical gate movement", "desktop widget placement", "signed device installation", "hosted GitHub Actions"]}
    logs = ROOT / "dist/validation"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "report.json").unlink(missing_ok=True)

    def run(label: str, command: list[str], cwd: Path) -> None:
        print(f"Checking {label}...", flush=True)
        with (logs / f"{label}.log").open("w") as log:
            subprocess.run(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, check=True)
        results["checks"].append(label)

    # Never write example credentials into the developer's working checkout.
    with tempfile.TemporaryDirectory(prefix="gate-release-") as directory:
        destination = Path(directory)
        with ZipFile(archive_path) as archive:
            names = archive.namelist()
            if len(set(names)) != len(names):
                raise ValueError("Duplicate archive member")
            for name in names:
                if not name.startswith("gate-opener/") or ".." in Path(name).parts:
                    raise ValueError("Unsafe archive path")
                if name.endswith("/.env") or name.endswith("/LocalConfiguration.swift") or "/.git/" in name:
                    raise ValueError("Private file in archive")
            archive.extractall(destination)
        source = destination / "gate-opener"
        manifest = {}
        for line in (source / "SHA256SUMS").read_text().splitlines():
            expected, name = line.split("  ", 1)
            if Path(name).is_absolute() or ".." in Path(name).parts:
                raise ValueError("Unsafe manifest path")
            manifest[name] = expected
            if hashlib.sha256((source / name).read_bytes()).hexdigest() != expected:
                raise ValueError(f"File checksum mismatch: {name}")
        actual = {p.relative_to(source).as_posix() for p in source.rglob("*") if p.is_file()} - {"SHA256SUMS"}
        if actual != set(manifest):
            raise ValueError("Manifest coverage mismatch")
        results["checks"].append("archive-integrity")
        run("extracted-tests", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], source)
        run("repackage", [sys.executable, "scripts/package_source.py"], source)
        if (source / "dist/gate-opener-source.zip").read_bytes() != archive_path.read_bytes():
            raise ValueError("Repackaging changed the archive")
        results["checks"].append("reproducible-package")
        if args.apple_builds:
            for platform, project, scheme, target in [
                ("macos", "GateOpenerMac", "GateOpenerMac", "platform=macOS"),
                ("ios", "GateOpener", "GateOpener", "generic/platform=iOS"),
            ]:
                run(f"generate-{platform}", ["xcodegen", "generate", "--spec", f"{platform}/project.yml"], source)
                run(f"build-{platform}", ["xcodebuild", "-project", f"{platform}/{project}.xcodeproj",
                    "-scheme", scheme, "-destination", target, "-derivedDataPath", str(destination / f"build-{platform}"),
                    "CODE_SIGNING_ALLOWED=NO", "build"], source)
    (logs / "report.json").write_text(json.dumps(results, indent=2) + "\n")
    print(f"Release validation passed. Report: {logs / 'report.json'}")


if __name__ == "__main__":
    main()
