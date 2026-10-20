import AppIntents
import WidgetKit

enum GateExtensionRefresh {
    static func request() {
        WidgetCenter.shared.reloadAllTimelines()
        #if os(iOS)
        if #available(iOS 18.0, *) {
            ControlCenter.shared.reloadAllControls()
        }
        #endif
    }
}

struct OpenGateIntent: AppIntent {
    static let title: LocalizedStringResource = "Open / Close"
    static let description = IntentDescription("Sends one momentary pulse to the front gate controller.")
    // This intentionally permits the Lock Screen control to run while locked.
    // Anyone with physical access to the phone can therefore activate the gate.
    static let authenticationPolicy: IntentAuthenticationPolicy = .alwaysAllowed
    static let openAppWhenRun = false

    // The app process can use our private URLSession trust evaluator, while the
    // WidgetKit extension process rejects the same valid Root YE chain. Keep the
    // interaction in the background, but run it in the containing app on iOS 27.
    #if os(iOS)
    @available(iOS 27.0, *)
    static let allowedExecutionTargets: IntentExecutionTargets = .main
    #endif

    func perform() async throws -> some IntentResult & ProvidesDialog {
        defer { GateExtensionRefresh.request() }
        // A read-only preflight safely warms the widget extension's DNS/TLS
        // connection. The state-changing open request is still sent once.
        GateActionFeedback.save(.sending, message: "Sending…")
        GateExtensionRefresh.request()
        do {
            let client = try GateAPIClient.configured()
            _ = try await client.isOnline()
            GatePositionStore.save(.unknown("movement"))
            GateExtensionRefresh.request()
            try await client.openGate()
            GateActionFeedback.save(.accepted, message: "Command sent. Refresh to check the gate.")
            return .result(dialog: "Command sent. Refresh to check the gate.")
        } catch {
            GateActionFeedback.save(.failed, message: error.localizedDescription)
            throw error
        }
    }
}

struct RefreshGateStatusIntent: AppIntent {
    static let title: LocalizedStringResource = "Check Gate Status"
    static let description = IntentDescription("Checks the gate camera without moving the gate.")
    static let openAppWhenRun = false

    #if os(iOS)
    @available(iOS 27.0, *)
    static let allowedExecutionTargets: IntentExecutionTargets = .main
    #endif

    func perform() async throws -> some IntentResult {
        do {
            let observation = try await GateAPIClient.configured().gatePosition()
            GatePositionStore.save(observation)
        } catch {
            GatePositionStore.save(.unknown("camera_unavailable"))
        }
        GateExtensionRefresh.request()
        return .result()
    }
}
