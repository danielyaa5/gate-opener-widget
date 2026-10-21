#!/usr/bin/env python3
"""Regenerate README screenshots, an animated GIF and an MP4, without UI capture."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.demo_camera import preview  # noqa: E402

POSES = [
    ("closed", "Check the gate and its latest picture", "closed", "closed", False, False, 3),
    ("moving-open", "Command sent; wait for the camera", "unknown", "closed", False, True, 3),
    ("open", "The next camera picture confirms Open", "open", "open", False, False, 3.5),
    ("moving-close", "Close Gate also waits for a fresh picture", "unknown", "open", False, True, 3),
    ("closed-again", "Closed again, with the original capture time", "closed", "closed", False, False, 3),
]


def run(command: list[str], **kwargs) -> None:
    subprocess.run(command, check=True, **kwargs)


def generate(output: Path, *, learning_only: bool = False) -> None:
    if sys.platform != "darwin":
        raise SystemExit("This recorder requires macOS and Xcode's command-line tools.")
    for tool in (("swiftc",) if learning_only else ("swiftc", "ffmpeg", "ffprobe")):
        if shutil.which(tool) is None:
            raise SystemExit(f"Missing {tool}. Install Xcode tools and ffmpeg before recording.")
    try:
        import PIL  # noqa: F401
    except ImportError:
        raise SystemExit("Install requirements-camera.txt in your virtual environment first.")

    sources = ["ios/GateOpener/App/ContentView.swift", "ios/GateOpenerWidget/GateOpenerWidget.swift",
               "ios/GateOpener/Shared/GatePosition.swift", "ios/GateOpener/Shared/GateActionFeedback.swift",
               "ios/GateOpener/Shared/GateSettings.swift", "ios/GateOpener/Shared/GateAPIClient.swift",
               "ios/GateOpener/Shared/OpenGateIntent.swift", "scripts/generate_demo_media.swift",
               "scripts/generate_demo_media.py", "scripts/demo_camera.py",
               "macos/GateOpenerMac/GateLearningController.swift"]
    with tempfile.TemporaryDirectory(prefix="gate-demo-media-") as directory:
        work = Path(directory)
        # Compile the actual presentation views, omitting app lifecycle and preview macros.
        dashboard = (ROOT / sources[0]).read_text().split("struct GateDashboardView: View", 1)[1].split("#Preview", 1)[0]
        (work / "Dashboard.swift").write_text("import AppKit\nimport SwiftUI\nstruct GateDashboardView: View" + dashboard)
        widget = (ROOT / sources[1]).read_text().split("struct GateWidgetView: View", 1)[1].split("#Preview", 1)[0]
        (work / "Widget.swift").write_text("import SwiftUI\nimport WidgetKit\nstruct GateWidgetView: View" + widget)
        learning = (ROOT / sources[-1]).read_text()
        prompt = learning.split("private struct GateLearningPrompt: View", 1)[1].split("struct GateLearningSettings: View", 1)[0]
        settings = learning.split("struct GateLearningSettings: View", 1)[1]
        # Real presentation code, with inert state/actions instead of the live
        # polling controller. Rendering must never open settings or a connection.
        stub = '''import AppKit
import SwiftUI
@MainActor final class GateLearningController: ObservableObject {
    static let shared = GateLearningController()
    @Published var enabled = true
    @Published var notice: String?
    @Published var saving = false
    @Published var answerError: String?
    func answer(_ state: String) {}
    func setEnabled(_ enabled: Bool) {}
}
'''
        (work / "Learning.swift").write_text(stub + "private struct GateLearningPrompt: View" + prompt
            + "struct GateLearningSettings: View" + settings + '''
@MainActor func demoLearningPrompt(_ candidate: GateLearningCandidate) -> some View {
    GateLearningPrompt(candidate: candidate, controller: GateLearningController.shared)
}
''')
        binary = work / "render"
        print("Rendering the real app and widget views with fictional camera data…", flush=True)
        run(["swiftc", "-parse-as-library", "-swift-version", "6", str(work / "Dashboard.swift"), str(work / "Widget.swift"), str(work / "Learning.swift"),
             *[str(ROOT / name) for name in sources[2:8]], "-o", str(binary)])
        # SwiftUI's relative Text uses the real clock, even in a time-frozen view.
        # Seed fresh timestamps after compiling, with no future-dated video frames.
        date = time.time()
        frames = []
        elapsed = 0.0
        for name, caption, state, picture, sending, accepted, duration in POSES:
            frames.append({"name": name, "caption": caption, "date": date, "sending": sending, "accepted": accepted,
                           "position": {"state": state, "observed_at": date - 2, "checked_at": date,
                                        "reason": "movement" if state == "unknown" else "matched"},
                           "preview": preview(picture, date - 2)})
            elapsed += duration
        (work / "frames.json").write_text(json.dumps(frames))
        run([str(binary), str(work)] + (["--learning-only"] if learning_only else []))
        # ImageRenderer may attach EXIF even to a generated PNG. Public demo
        # images retain pixels only, without capture/device metadata.
        from PIL import Image
        for path in work.glob("*.png"):
            with Image.open(path) as rendered:
                rendered.save(path.with_suffix(".clean.png"), format="PNG", exif=b"")
            path.with_suffix(".clean.png").replace(path)
        if learning_only:
            outputs = ["learning-mode.png"]
            report = {"description": "Production learning-dialog view rendered offscreen with a fictional camera image and inert actions; no live settings, images or network access.",
                      "sources": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources},
                      "outputs": {name: hashlib.sha256((work / name).read_bytes()).hexdigest() for name in outputs}}
            (work / "learning-media.json").write_text(json.dumps(report, indent=2) + "\n")
            output.mkdir(parents=True, exist_ok=True)
            for name in [*outputs, "learning-media.json"]:
                shutil.copy2(work / name, output / name)
            print(f"Saved learning screenshot and source hashes in {output}")
            return
        shutil.copy2(work / "closed.png", work / "mac-app.png")

        sequence = "".join(f"file '{name}.png'\nduration {duration}\n" for name, *_, duration in POSES)
        sequence += f"file '{POSES[-1][0]}.png'\n"
        (work / "sequence.txt").write_text(sequence)
        print("Encoding MP4 and animated README preview…", flush=True)
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(work / "sequence.txt"),
             "-t", str(elapsed), "-vf", "fps=24,pad=ceil(iw/2)*2:ceil(ih/2)*2,format=yuv420p", "-c:v", "libx264", "-crf", "22",
             "-movflags", "+faststart", "-map_metadata", "-1", "-metadata", "title=Gate Opener simulated demo", str(work / "demo.mp4")])
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(work / "demo.mp4"), "-filter_complex",
             "fps=6,scale=400:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=3",
             "-loop", "0", str(work / "demo.gif")])
        metadata = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(work / "demo.mp4")]))
        stream = next(s for s in metadata["streams"] if s["codec_type"] == "video")
        if stream["codec_name"] != "h264" or abs(float(metadata["format"]["duration"]) - elapsed) > 0.1:
            raise RuntimeError("Generated demo did not validate")

        outputs = ["mac-app.png", "mac-widget.png", "learning-mode.png", "demo.gif", "demo.mp4"]
        report = {"description": "Offscreen renders of production SwiftUI views with fictional camera images and scripted states.",
                  "duration_seconds": elapsed, "video_dimensions": [stream["width"], stream["height"]],
                  "sources": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources},
                  "outputs": {name: hashlib.sha256((work / name).read_bytes()).hexdigest() for name in outputs}}
        (work / "demo-media.json").write_text(json.dumps(report, indent=2) + "\n")
        output.mkdir(parents=True, exist_ok=True)
        for name in [*outputs, "demo-media.json"]:
            temporary = output / ("." + name + ".tmp")
            shutil.copy2(work / name, temporary)
            temporary.replace(output / name)
        print(f"Saved screenshots, GIF, MP4 and source hashes in {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/images", help="Destination (default: docs/images)")
    parser.add_argument("--learning-only", action="store_true", help="Render just the learning dialog, without regenerating video or other screenshots")
    args = parser.parse_args()
    generate(args.output.resolve(), learning_only=args.learning_only)


if __name__ == "__main__":
    main()
