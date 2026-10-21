import AppKit
import SwiftUI
import WidgetKit

private struct DemoFrame: Decodable {
    let name: String
    let caption: String
    let date: TimeInterval
    let position: GatePositionObservation
    let preview: GateCameraPreview
    let sending: Bool
    let accepted: Bool
}

private struct DemoWindow: View {
    let frame: DemoFrame

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 7) {
                ForEach([Color.red, .yellow, .green], id: \.self) { color in
                    Circle().fill(color.opacity(0.75)).frame(width: 10, height: 10)
                }
                Spacer()
                Text("Front Gate").font(.caption.weight(.semibold))
                Spacer()
                Color.clear.frame(width: 44, height: 1)
            }
            .padding(14)
            .background(.quaternary.opacity(0.35))

            VStack(spacing: 5) {
                Text("SIMULATED DEMO")
                    .font(.system(size: 9, weight: .bold, design: .rounded))
                    .foregroundStyle(.secondary)
                Text(frame.caption)
                    .font(.system(size: 14, weight: .semibold))
            }
            .frame(height: 52)

            GateDashboardView(
                position: frame.position,
                feedback: frame.sending || frame.accepted ? GateActionFeedback(
                    date: Date(timeIntervalSince1970: frame.date),
                    outcome: frame.sending ? .sending : .accepted,
                    message: frame.sending ? "Sending…" : "Command sent. Refresh to check the gate."
                ) : nil,
                isOpening: frame.sending,
                isOnline: true,
                date: Date(timeIntervalSince1970: frame.date),
                cameraPreview: frame.preview
            ).dashboardContent
            Spacer(minLength: 0)
        }
        .frame(width: 400, height: 920, alignment: .top)
        .background(Color(nsColor: .windowBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 18))
        .overlay(RoundedRectangle(cornerRadius: 18).strokeBorder(.black.opacity(0.08), lineWidth: 1))
        .environment(\.colorScheme, .light)
        .environment(\.locale, Locale(identifier: "en_US"))
        .environment(\.timeZone, TimeZone(secondsFromGMT: 0)!)
    }
}

@main
struct GenerateDemoMedia {
    @MainActor
    static func write<V: View>(_ view: V, to path: URL) throws {
        let renderer = ImageRenderer(content: view)
        renderer.scale = 2
        guard let image = renderer.cgImage,
              let png = NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:]) else {
            throw NSError(domain: "DemoMedia", code: 1, userInfo: [NSLocalizedDescriptionKey: "Could not render \(path.lastPathComponent)"])
        }
        try png.write(to: path)
    }

    @MainActor
    static func main() throws {
        // Offscreen rendering only: no window, screen recording, Keychain or app launch.
        _ = NSApplication.shared
        let folder = URL(fileURLWithPath: CommandLine.arguments[1])
        let frames = try JSONDecoder().decode([DemoFrame].self, from: Data(contentsOf: folder.appendingPathComponent("frames.json")))
        let first = frames[0]
        let candidate = GateLearningCandidate(id: "fictional-learning-example", capturedAt: 1_792_517_400,
                                             imageBase64: first.preview.imageBase64!, cameraName: "Demo camera")
        try write(
            VStack(spacing: 0) {
                HStack(spacing: 7) {
                    ForEach([Color.red, .yellow, .green], id: \.self) { color in
                        Circle().fill(color.opacity(0.75)).frame(width: 10, height: 10)
                    }
                    Spacer()
                    Text("Help Gate Opener learn").font(.caption.weight(.semibold))
                    Spacer()
                    Color.clear.frame(width: 44, height: 1)
                }.padding(14).background(.quaternary.opacity(0.35))
                Text("SIMULATED DEMO").font(.system(size: 9, weight: .bold)).foregroundStyle(.secondary).padding(.top, 14)
                demoLearningPrompt(candidate)
            }
            .frame(width: 600).background(Color(nsColor: .windowBackgroundColor))
            .clipShape(RoundedRectangle(cornerRadius: 18))
            .overlay(RoundedRectangle(cornerRadius: 18).strokeBorder(.black.opacity(0.08), lineWidth: 1))
            .environment(\.colorScheme, .light)
            .environment(\.locale, Locale(identifier: "en_US"))
            .environment(\.timeZone, TimeZone(secondsFromGMT: 0)!),
            to: folder.appendingPathComponent("learning-mode.png")
        )
        if CommandLine.arguments.contains("--learning-only") { return }
        for frame in frames {
            try write(DemoWindow(frame: frame), to: folder.appendingPathComponent("\(frame.name).png"))
        }
        let closed = frames[0]
        try write(
            GateWidgetView(isConfigured: true, feedback: nil, position: closed.position,
                           date: Date(timeIntervalSince1970: closed.date))
                .padding(16).frame(width: 188, height: 188)
                .background(Color(nsColor: .windowBackgroundColor), in: RoundedRectangle(cornerRadius: 20))
                .environment(\.colorScheme, .light),
            to: folder.appendingPathComponent("mac-widget.png")
        )
        print("Rendered \(frames.count) app frames and the widget.")
    }
}
