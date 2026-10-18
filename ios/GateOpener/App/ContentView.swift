import SwiftUI

struct ContentView: View {
    @State private var isChecking = false
    @State private var isOpening = false
    @State private var isOnline: Bool?
    @State private var errorMessage: String?
    @State private var showConfirmation = false
    @State private var connection = GateSettings.load()
    @State private var showSettings = false

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
    }

    private var gateControls: some View {
            VStack(spacing: 24) {
                Spacer()

                Image(systemName: "door.garage.closed")
                    .font(.system(size: 72))
                    .symbolRenderingMode(.hierarchical)
                    .foregroundStyle(.tint)

                VStack(spacing: 8) {
                    Text("Front Gate")
                        .font(.largeTitle.bold())
                    statusLabel
                }

                Button {
                    showConfirmation = true
                } label: {
                    HStack {
                        if isOpening {
                            ProgressView()
                                .tint(.white)
                            Text("Sending…")
                        } else {
                            Label("Open / Close", systemImage: "door.garage.open")
                        }
                    }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 8)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(isOpening)
                .confirmationDialog(
                    "Open or close the front gate?",
                    isPresented: $showConfirmation,
                    titleVisibility: .visible
                ) {
                    Button("Open / Close") {
                        Task { await openGate() }
                    }
                }

                Text("Sends one pulse. The gate controller determines movement.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)

                Button("Check Connection") {
                    Task { await checkConnection() }
                }
                .disabled(isChecking)

                Spacer()
            }
            .padding(24)
            .navigationTitle("Gate Opener")
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
                .frame(minWidth: 420, minHeight: 380)
                #endif
            }
            .alert("Gate Error", isPresented: errorIsPresented) {
                Button("OK", role: .cancel) {}
            } message: {
                Text(errorMessage ?? "Unknown error")
            }
            .task {
                await checkConnection()
            }
    }

    @ViewBuilder
    private var statusLabel: some View {
        if isChecking {
            Label("Checking…", systemImage: "arrow.trianglehead.2.clockwise.rotate.90")
                .foregroundStyle(.secondary)
        } else if isOnline == true {
            Label("Mac bridge online", systemImage: "checkmark.circle.fill")
                .foregroundStyle(.green)
        } else if isOnline == false {
            Label("Mac bridge unavailable", systemImage: "exclamationmark.triangle.fill")
                .foregroundStyle(.orange)
        } else {
            Text("Not checked")
                .foregroundStyle(.secondary)
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
        isChecking = true
        defer { isChecking = false }
        do {
            isOnline = try await GateAPIClient.configured().isOnline()
        } catch {
            isOnline = false
            errorMessage = error.localizedDescription
        }
    }

    @MainActor
    private func openGate() async {
        isOpening = true
        defer { isOpening = false }
        do {
            try await GateAPIClient.configured().openGate()
            isOnline = true
        } catch {
            isOnline = false
            errorMessage = error.localizedDescription
        }
    }
}

#Preview {
    ContentView()
}
