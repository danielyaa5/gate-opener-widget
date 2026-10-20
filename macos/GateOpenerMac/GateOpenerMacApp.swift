import AppKit
import SwiftUI

@MainActor
final class GateOpenerMacDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }
}

@main
struct GateOpenerMacApp: App {
    @NSApplicationDelegateAdaptor(GateOpenerMacDelegate.self) private var appDelegate

    init() {
        GateExtensionRefresh.request()
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
                .frame(minWidth: 320, minHeight: 560)
        }
        .defaultSize(width: 400, height: 800)
        .windowResizability(.contentSize)
    }
}
