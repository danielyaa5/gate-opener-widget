import SwiftUI
#if os(macOS)
import AppKit
#else
import UIKit
#endif

struct ContentView: View {
    @Environment(\.scenePhase) private var scenePhase
    @State private var isChecking = false
    @State private var isOpening = false
    @State private var isOnline: Bool?
    @State private var errorMessage: String?
    @State private var showConfirmation = false
    @State private var connection = GateSettings.load()
    @State private var showSettings = false
    @State private var feedback = GateActionFeedback.load()
    @State private var position = GatePositionStore.load()
    @State private var cameraPreview: GateCameraPreview?
    @State private var lastCommandAt = Date.distantPast
    @State private var lastWidgetRefreshAt = Date.distantPast

    var body: some View {
        NavigationStack {
            if connection == nil {
                SetupView(existing: nil) { saved in
                    connection = saved
                    Task { await checkConnection() }
                }
            } else {
                gateControls
            }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                connection = GateSettings.load()
                feedback = GateActionFeedback.load()
                position = GatePositionStore.load()
            } else {
                cameraPreview = nil
            }
        }
    }

    private var gateControls: some View {
        TimelineView(.periodic(from: .now, by: 30)) { timeline in
            GateDashboardView(
                position: position,
                feedback: feedback,
                isChecking: isChecking,
                isOpening: isOpening,
                isOnline: isOnline,
                date: timeline.date,
                cameraPreview: cameraPreview,
                refresh: { Task { await checkConnection() } },
                operate: { showConfirmation = true }
            )
        }
            .confirmationDialog(
                (position ?? .unknown()).confirmationTitle(),
                isPresented: $showConfirmation,
                titleVisibility: .visible
            ) {
                Button((position ?? .unknown()).actionTitle()) {
                    Task { await openGate() }
                }
            }
            .navigationTitle("Front Gate")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                Button {
                    showSettings = true
                } label: {
                    Label("Bridge Settings", systemImage: "gearshape")
                }
            }
            .sheet(isPresented: $showSettings) {
                NavigationStack {
                    SetupView(existing: connection) { saved in
                        connection = saved
                        position = GatePositionStore.save(.unknown())
                        cameraPreview = nil
                        showSettings = false
                        if saved != nil {
                            Task { await checkConnection() }
                        }
                    }
                    .toolbar {
                        ToolbarItem(placement: .cancellationAction) {
                            Button("Cancel") { showSettings = false }
                        }
                    }
                }
                #if os(macOS)
                .frame(minWidth: 420, minHeight: 440)
                #endif
            }
            .alert("Gate Error", isPresented: errorIsPresented) {
                Button("OK", role: .cancel) {}
            } message: {
                Text(errorMessage ?? "Unknown error")
            }
            .task(id: scenePhase) {
                guard scenePhase == .active else { return }
                await checkConnection()
                while !Task.isCancelled {
                    do { try await Task.sleep(for: .seconds(10)) }
                    catch { return }
                    await checkCamera()
                }
            }
    }

    private var errorIsPresented: Binding<Bool> {
        Binding(
            get: { errorMessage != nil },
            set: { if !$0 { errorMessage = nil } }
        )
    }

    @MainActor
    private func checkConnection() async {
        guard !isChecking else { return }
        isChecking = true
        defer { isChecking = false }
        do {
            isOnline = try await GateAPIClient.configured().isOnline()
        } catch {
            guard !Task.isCancelled else { return }
            isOnline = false
            errorMessage = error.localizedDescription
        }
        await loadCameraPosition(forceWidgetRefresh: true)
    }

    @MainActor
    private func checkCamera() async {
        guard !isChecking, !isOpening, connection != nil else { return }
        isChecking = true
        defer { isChecking = false }
        await loadCameraPosition()
    }

    @MainActor
    private func loadCameraPosition(forceWidgetRefresh: Bool = false) async {
        guard let requestedConnection = connection else { return }
        let started = Date.now
        let observation: GatePositionObservation
        do {
            observation = try await GateAPIClient.configured().gatePosition()
        } catch {
            guard !Task.isCancelled else { return }
            observation = .unknown("camera_unavailable")
        }
        // A camera response that started before a command is no longer current.
        guard !Task.isCancelled, !isOpening, started > lastCommandAt else { return }
        let changed = position?.currentState() != observation.currentState()
        position = GatePositionStore.save(observation)
        if forceWidgetRefresh || changed || Date.now.timeIntervalSince(lastWidgetRefreshAt) >= 60 {
            lastWidgetRefreshAt = .now
            GateExtensionRefresh.request()
        }
        do {
            let preview = try await GateAPIClient.configured().cameraPreview()
            guard !Task.isCancelled, connection == requestedConnection else { return }
            cameraPreview = preview
        } catch {
            guard !Task.isCancelled, connection == requestedConnection else { return }
            // Never keep displaying a picture after the bridge becomes unreachable.
            cameraPreview = nil
        }
    }

    @MainActor
    private func openGate() async {
        isOpening = true
        feedback = GateActionFeedback.save(.sending, message: "Sending…")
        defer {
            isOpening = false
            GateExtensionRefresh.request()
        }
        do {
            // A lost response can still mean the gate moved. Stop displaying
            // the earlier observation as soon as a command is attempted.
            lastCommandAt = .now
            position = GatePositionStore.save(.unknown("movement"))
            try await GateAPIClient.configured().openGate()
            isOnline = true
            feedback = GateActionFeedback.save(.accepted, message: "Command sent. Refresh to check the gate.")
        } catch {
            isOnline = false
            errorMessage = error.localizedDescription
            feedback = GateActionFeedback.save(.failed, message: error.localizedDescription)
        }
    }
}

/// Shared by the phone and Mac; rendering this view never sends a gate command.
struct GateDashboardView: View {
    var position: GatePositionObservation?
    var feedback: GateActionFeedback?
    var isChecking = false
    var isOpening = false
    var isOnline: Bool?
    var date = Date.now
    var cameraPreview: GateCameraPreview?
    var refresh: () -> Void = {}
    var operate: () -> Void = {}

    private var observation: GatePositionObservation { position ?? .unknown() }
    private var state: String { observation.currentState(at: date) }
    private var statusColor: Color {
        switch state {
        case "closed": return .green
        case "open": return .orange
        default: return .secondary
        }
    }
    private var title: String {
        switch state {
        case "open": return "Open"
        case "closed": return "Closed"
        default: return observation.title(at: date)
        }
    }

    var body: some View {
        GeometryReader { geometry in
            ScrollView {
                dashboardContent
                    .frame(maxWidth: .infinity, minHeight: geometry.size.height)
            }
            .scrollBounceBehavior(.basedOnSize)
        }
        .background(.quaternary.opacity(0.25))
    }

    var dashboardContent: some View {
        VStack(spacing: 24) {
            VStack(spacing: 22) {
                HStack {
                    Label("GATE STATUS", systemImage: "video")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                    Spacer()
                    if state != "unknown" {
                        Text("From camera")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }

                VStack(spacing: 16) {
                    if previewImage == nil {
                        Image(systemName: observation.symbol(at: date))
                            .font(.system(size: 52, weight: .light))
                            .symbolRenderingMode(.hierarchical)
                            .foregroundStyle(statusColor)
                            .frame(width: 108, height: 108)
                            .background(statusColor.opacity(0.10), in: Circle())
                            .accessibilityHidden(true)
                    }

                    VStack(spacing: 7) {
                        Text(title)
                            .font(state == "unknown" ? .title2.weight(.semibold) : .largeTitle.weight(.semibold))
                            .fontDesign(.rounded)
                            .foregroundStyle(.primary)
                            .multilineTextAlignment(.center)
                            .fixedSize(horizontal: false, vertical: true)
                            .accessibilityIdentifier("gatePosition")
                        if let observed = position?.observedDate {
                            Text("Last seen \(observed, style: .relative) ago")
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                        } else {
                            Text("Refresh for the latest camera view")
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                                .multilineTextAlignment(.center)
                        }
                    }
                }
                .frame(maxWidth: .infinity)

                Button(action: refresh) {
                    HStack(spacing: 8) {
                        if isChecking {
                            ProgressView().controlSize(.small)
                            Text("Checking camera…")
                        } else {
                            Image(systemName: "arrow.clockwise")
                            Text("Refresh Status")
                        }
                    }
                    .font(.subheadline.weight(.medium))
                    .frame(maxWidth: .infinity, minHeight: 28)
                }
                .buttonStyle(.bordered)
                .controlSize(.large)
                .disabled(isChecking)
                .accessibilityHint("Checks the camera without moving the gate")
            }
            .padding(24)
            .background(.background, in: RoundedRectangle(cornerRadius: 24))
            .overlay {
                RoundedRectangle(cornerRadius: 24)
                    .strokeBorder(.primary.opacity(0.06), lineWidth: 1)
            }

            VStack(spacing: 12) {
                Button(action: operate) {
                    HStack(spacing: 9) {
                        if isOpening {
                            ProgressView().tint(.white)
                            Text("Sending…")
                        } else {
                            Image(systemName: observation.actionSymbol(at: date))
                                .accessibilityHidden(true)
                            Text(observation.actionTitle(at: date))
                        }
                    }
                    .font(.headline)
                    .frame(maxWidth: .infinity, minHeight: 38)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(isOpening)
                .accessibilityHint("Asks for confirmation before sending a gate command")

                Text("Tap once to open or close the gate.")
                    .font(.caption)
                    .foregroundStyle(.secondary)

                if let feedback {
                    Label(feedback.displayMessage, systemImage:
                        feedback.outcome == .accepted ? "checkmark.circle" :
                        feedback.isSending ? "clock" : "exclamationmark.triangle")
                        .font(.caption)
                        .foregroundStyle(feedback.outcome == .failed ? Color.orange : Color.secondary)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("gateActionResult")
                }
            }

            cameraCard

            #if os(macOS)
            GateLearningSettings()
            #endif

            Label(isOnline == true ? "Connected" : isOnline == false ? "Connection unavailable" : "Connecting…",
                  systemImage: isOnline == true ? "checkmark.circle.fill" : isOnline == false ? "wifi.exclamationmark" : "network")
                .font(.caption)
                .foregroundStyle(isOnline == true ? Color.secondary : Color.orange)
                .padding(.top, 2)
        }
        .padding(24)
        .frame(maxWidth: 440)
    }

    private var cameraCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label(cameraPreview?.cameraName ?? "Gate camera", systemImage: "video")
                    .font(.subheadline.weight(.semibold))
                Spacer()
                Text("Latest picture")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if let image = previewImage, let captured = cameraPreview?.capturedDate(at: date) {
                image
                    .resizable()
                    .scaledToFit()
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                    .accessibilityLabel("Latest camera picture")
                HStack {
                    Text("Captured \(captured, style: .relative) ago")
                    Spacer()
                    if date.timeIntervalSince(captured) > 30 {
                        Text("Older image").foregroundStyle(.orange)
                    }
                }
                .font(.caption)
                .foregroundStyle(.secondary)
                Text(captured, format: .dateTime.month(.abbreviated).day().hour().minute().second())
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            } else {
                VStack(spacing: 8) {
                    Image(systemName: "photo").font(.title2)
                    Text("Picture unavailable")
                        .font(.subheadline)
                }
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, minHeight: 120)
                .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 12))
            }
            Link(destination: cameraPreview?.ringURL ?? URL(string: "https://account.ring.com/account/dashboard")!) {
                Label("View live in Ring", systemImage: "arrow.up.right.video")
                    .font(.subheadline.weight(.medium))
                    .frame(maxWidth: .infinity, minHeight: 28)
            }
            .buttonStyle(.bordered)
            .controlSize(.large)
            .accessibilityHint("Opens the camera feed in Ring")
        }
        .padding(16)
        .background(.background, in: RoundedRectangle(cornerRadius: 20))
        .overlay {
            RoundedRectangle(cornerRadius: 20)
                .strokeBorder(.primary.opacity(0.06), lineWidth: 1)
        }
    }

    private var previewImage: Image? {
        guard let data = cameraPreview?.imageData(at: date) else { return nil }
        #if os(macOS)
        guard let image = NSImage(data: data) else { return nil }
        return Image(nsImage: image)
        #else
        guard let image = UIImage(data: data) else { return nil }
        return Image(uiImage: image)
        #endif
    }
}

#Preview {
    ContentView()
}
