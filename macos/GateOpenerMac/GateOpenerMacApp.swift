import AppKit
import SwiftUI

@MainActor
final class GateOpenerMacDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        GateLearningController.shared.start()
        NotificationCenter.default.addObserver(forName: NSWindow.willCloseNotification, object: nil, queue: .main) { _ in
            Task { @MainActor in
                // Window visibility updates after the close notification.
                await Task.yield()
                Self.updateDockVisibility()
            }
        }
        NotificationCenter.default.addObserver(forName: NSWindow.didBecomeKeyNotification, object: nil, queue: .main) { _ in
            Task { @MainActor in Self.updateDockVisibility() }
        }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        sender.setActivationPolicy(.regular)
        return true
    }

    static func updateDockVisibility() {
        let visible = NSApp.windows.contains { $0.isVisible && $0.canBecomeKey }
        NSApp.setActivationPolicy(visible ? .regular : .accessory)
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
