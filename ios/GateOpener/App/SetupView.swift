import SwiftUI

/// Connects the app and its widget to a bridge. The connection is saved to the
/// Keychain and shared with the widget through a Keychain access group.
struct SetupView: View {
    var existing: GateConnection?
    var onSave: (GateConnection?) -> Void

    @State private var urlString = ""
    @State private var secret = ""
    @State private var errorMessage: String?
    @State private var showManualEntry = false

    var body: some View {
        Form {
            Section {
                Label("Connect your gate", systemImage: "link.circle.fill")
                    .font(.title3.weight(.semibold))
                Text("Paste the setup code from your bridge Mac to connect this app and its widget.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }

            Section {
                PasteButton(payloadType: String.self) { strings in
                    guard let code = strings.first else { return }
                    Task { @MainActor in apply(setupCode: code) }
                }
                .labelStyle(.titleAndIcon)
                .controlSize(.large)
            } header: {
                Text("Setup code")
            } footer: {
                Text("Your connection is saved securely on this device.")
            }

            Section {
                DisclosureGroup("Connection details", isExpanded: $showManualEntry) {
                    TextField("Bridge URL", text: $urlString, prompt: Text(verbatim: "https://your-bridge.example"))
                        .textContentType(.URL)
                        .autocorrectionDisabled()
                        #if os(iOS)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        #endif
                    SecureField("Connection key", text: $secret, prompt: Text("Paste your connection key"))
                        .autocorrectionDisabled()
                }
            }

            if let errorMessage {
                Text(errorMessage)
                    .foregroundStyle(.red)
            }

            Section {
                Button("Save Connection") { save() }
                    .disabled(urlString.isEmpty || secret.isEmpty)
                if existing != nil {
                    Button("Disconnect Gate", role: .destructive) {
                        GateSettings.clear()
                        onSave(nil)
                    }
                }
            }
        }
        .formStyle(.grouped)
        .navigationTitle(existing == nil ? "Set Up Gate" : "Connection")
        .onAppear {
            if let existing, urlString.isEmpty {
                urlString = existing.bridgeURL.absoluteString
                secret = existing.secret
            }
        }
    }

    private func apply(setupCode: String) {
        do {
            let connection = try GateSettings.connection(setupCode: setupCode)
            urlString = connection.bridgeURL.absoluteString
            secret = connection.secret
            store(connection)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func save() {
        do {
            store(try GateSettings.connection(urlString: urlString, secret: secret))
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func store(_ connection: GateConnection) {
        do {
            try GateSettings.save(connection)
            errorMessage = nil
            GateExtensionRefresh.request()
            onSave(connection)
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
