import AppKit
import SwiftUI

/// Runs only in the Mac app, including when its main window is closed.
@MainActor
final class GateLearningController: NSObject, ObservableObject, NSWindowDelegate {
    static let shared = GateLearningController()
    @Published var enabled = true
    @Published var notice: String?
    @Published var saving = false
    @Published var answerError: String?
    private var task: Task<Void, Never>?
    private var promptWindow: NSWindow?
    private var candidate: GateLearningCandidate?
    private var shownIDs = Set<String>()
    private var connection: GateConnection?

    func start() {
        guard task == nil else { return }
        task = Task {
            while !Task.isCancelled {
                await poll()
                do { try await Task.sleep(for: .seconds(10)) }
                catch { return }
            }
        }
    }

    func poll() async {
        guard let requested = GateSettings.load(), !saving else { return }
        if connection != requested {
            closePrompt()
            shownIDs.removeAll()
            connection = requested
        }
        do {
            let status = try await GateAPIClient.configured().learningStatus()
            guard GateSettings.load() == requested else { return }
            enabled = status.enabled
            if !enabled || (candidate != nil && candidate?.id != status.candidate?.id) { closePrompt() }
            guard enabled, promptWindow == nil, let next = status.candidate,
                  !shownIDs.contains(next.id), next.imageData != nil else { return }
            show(next)
        } catch {
            // Ordinary camera/network gaps must not generate another popup.
        }
    }

    func setEnabled(_ value: Bool) {
        Task {
            do {
                try await GateAPIClient.configured().setLearningEnabled(value)
                enabled = value
                notice = value ? "Learning is on. Unclear pictures will ask for your help." : "Learning is off."
                if !value { closePrompt() }
                else { await poll() }
            } catch { notice = error.localizedDescription }
        }
    }

    private func show(_ next: GateLearningCandidate) {
        candidate = next
        shownIDs.insert(next.id)
        answerError = nil
        let window = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 600, height: 540),
                             styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Help Gate Opener learn"
        window.isReleasedWhenClosed = false
        window.delegate = self
        let hosting = NSHostingView(rootView: GateLearningPrompt(candidate: next, controller: self))
        window.contentView = hosting
        window.setContentSize(hosting.fittingSize)
        window.center()
        promptWindow = window
        NSApp.setActivationPolicy(.regular)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        NSApp.requestUserAttention(.informationalRequest)
    }

    func answer(_ state: String) {
        guard let candidate, !saving else { return }
        if state == "skip" {
            // Dismiss immediately even if the bridge went offline. This ID
            // remains suppressed locally while the best-effort skip is sent.
            notice = "Picture skipped."
            closePrompt()
            Task { try? await GateAPIClient.configured().answerLearning(candidateID: candidate.id, state: "skip") }
            return
        }
        saving = true
        answerError = nil
        Task {
            defer { saving = false }
            do {
                try await GateAPIClient.configured().answerLearning(candidateID: candidate.id, state: state)
                notice = state == "skip" ? "Picture skipped." : "Saved as \(state == "open" ? "Open" : "Closed"). Future pictures can use this example."
                closePrompt()
                GateExtensionRefresh.request()
            } catch { answerError = error.localizedDescription }
        }
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        answer("skip")
        return false
    }

    private func closePrompt() {
        promptWindow?.delegate = nil
        promptWindow?.close()
        promptWindow = nil
        candidate = nil
        GateOpenerMacDelegate.updateDockVisibility()
    }
}

private struct GateLearningPrompt: View {
    let candidate: GateLearningCandidate
    @ObservedObject var controller: GateLearningController

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Is the gate open or closed in this picture?")
                .font(.title2.weight(.semibold))
            Text("I couldn’t confidently match this view. Choose only if it is fully open or fully closed.")
                .foregroundStyle(.secondary)
            if let data = candidate.imageData, let image = NSImage(data: data) {
                Image(nsImage: image).resizable().scaledToFit().frame(maxHeight: 300)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                    .accessibilityLabel("Unclear gate picture to label")
            }
            Text("\(candidate.cameraName) • \(Date(timeIntervalSince1970: candidate.capturedAt), format: .dateTime.month(.abbreviated).day().hour().minute().second())")
                .font(.caption).foregroundStyle(.secondary)
            Text("This saves a private reference photo on your bridge Mac. It does not move the gate.")
                .font(.callout).foregroundStyle(.secondary)
            if let error = controller.answerError {
                Text(error).font(.callout).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true)
            }
            HStack(spacing: 12) {
                Button("Skip / Not sure") { controller.answer("skip") }
                    .accessibilityIdentifier("learningSkip")
                Spacer()
                Button("Fully Closed") { controller.answer("closed") }
                    .accessibilityIdentifier("learningClosed")
                Button("Fully Open") { controller.answer("open") }
                    .accessibilityIdentifier("learningOpen")
                    .buttonStyle(.borderedProminent)
            }
            .controlSize(.large)
            .disabled(controller.saving)
            if controller.saving { ProgressView("Saving your answer…").controlSize(.small) }
        }
        .padding(24)
        .frame(width: 600)
        .fixedSize(horizontal: false, vertical: true)
    }
}

struct GateLearningSettings: View {
    @ObservedObject private var learning = GateLearningController.shared
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Toggle("Learning mode", isOn: Binding(get: { learning.enabled }, set: { learning.setEnabled($0) }))
                .accessibilityIdentifier("learningEnabled")
            Text("Ask me to label unclear pictures on this Mac. Runs quietly with the main window closed; at most one new question every 15 minutes.")
                .font(.caption).foregroundStyle(.secondary)
            if let notice = learning.notice { Text(notice).font(.caption).foregroundStyle(.secondary) }
        }
        .padding(16)
        .background(.background, in: RoundedRectangle(cornerRadius: 20))
    }
}
