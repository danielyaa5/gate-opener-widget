import SwiftUI

/// Connects the app and its widget to a bridge. The connection is saved to the
/// Keychain and shared with the widget through the app group.
struct SetupView: View {
    var existing: GateConnection?
    var onSave: (GateConnection?) -> Void

    @State private var urlString = ""
    @State private var secret = ""
    @State private var errorMessage: String?

    var body: some View {
        Form {
            Section {
                PasteButton(payloadType: String.self) { strings in
                    guard let code = strings.first else { return }
                    Task { @MainActor in apply(setupCode: code) }
                }
                .labelStyle(.titleAndIcon)
            } header: {
                Text("Setup code")
            } footer: {
                Text("On the bridge Mac, run `.venv/bin/python scripts/setup_code.py --url https://…` and copy the code it prints.")
            }

            Section("Or enter manually") {
                TextField("Bridge URL", text: $urlString, prompt: Text(verbatim: "https://your-mac.your-tailnet.ts.net"))
                    .textContentType(.URL)
                    .autocorrectionDisabled()
                    #if os(iOS)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    #endif
                SecureField("API secret", text: $secret, prompt: Text("GATE_API_SECRET from .env"))
                    .autocorrectionDisabled()
            }

            if let errorMessage {
                Text(errorMessage)
                    .foregroundStyle(.red)
            }

            Section {
                Button("Save") { save() }
                    .disabled(urlString.isEmpty || secret.isEmpty)
                if existing != nil {
                    Button("Remove Bridge", role: .destructive) {
                        GateSettings.clear()
                        onSave(nil)
                    }
                }
            }
        }
        .formStyle(.grouped)
        .navigationTitle("Bridge")
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
