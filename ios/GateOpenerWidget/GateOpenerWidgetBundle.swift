import SwiftUI
import WidgetKit

@main
struct GateOpenerWidgetBundle: WidgetBundle {
    var body: some Widget {
        GateOpenerWidget()
        if #available(iOS 18.0, *) {
            GateOpenerControl()
        }
    }
}
