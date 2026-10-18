import SwiftUI

@main
struct GateOpenerMacApp: App {
    init() {
        GateExtensionRefresh.request()
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
                .frame(minWidth: 320, minHeight: 420)
        }
        .defaultSize(width: 360, height: 480)
        .windowResizability(.contentSize)
    }
}
