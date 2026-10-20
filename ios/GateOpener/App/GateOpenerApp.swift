import SwiftUI

@main
struct GateOpenerApp: App {
    init() {
        // Re-register the widget and control whenever the containing app launches.
        // This helps iOS discard cached extension state after an app update.
        GateLocationCleanup.disableLegacyMonitoring()
        GateExtensionRefresh.request()
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
        }
    }
}
