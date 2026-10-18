import SwiftUI
import WidgetKit

@available(iOS 18.0, *)
struct GateOpenerControl: ControlWidget {
    static let kind = "com.danielyakobian.GateOpener.control"

    var body: some ControlWidgetConfiguration {
        StaticControlConfiguration(kind: Self.kind) {
            ControlWidgetButton(action: OpenGateIntent()) {
                Label("Open / Close", systemImage: "door.garage.open")
            }
        }
        .displayName("Open / Close")
        .description("Send an open/close pulse from the Lock Screen or Control Center.")
    }
}
