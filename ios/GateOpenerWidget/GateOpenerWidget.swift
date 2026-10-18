import SwiftUI
import WidgetKit

struct GateEntry: TimelineEntry {
    let date: Date
    var isConfigured = GateSettings.load() != nil
}

struct GateTimelineProvider: TimelineProvider {
    func placeholder(in context: Context) -> GateEntry {
        GateEntry(date: .now)
    }

    func getSnapshot(in context: Context, completion: @escaping (GateEntry) -> Void) {
        completion(GateEntry(date: .now))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<GateEntry>) -> Void) {
        let nextRefresh = Calendar.current.date(byAdding: .day, value: 1, to: .now) ?? .now
        completion(Timeline(entries: [GateEntry(date: .now)], policy: .after(nextRefresh)))
    }
}

struct GateOpenerWidget: Widget {
    let kind = "GateOpenerWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: GateTimelineProvider()) { entry in
            GateWidgetView(isConfigured: entry.isConfigured)
                .containerBackground(.fill.tertiary, for: .widget)
        }
        .configurationDisplayName("Gate Opener")
        .description("Send an open/close pulse without launching the app.")
        .supportedFamilies([.systemSmall])
    }
}

struct GateWidgetView: View {
    var isConfigured = true
    @Environment(\.widgetRenderingMode) private var renderingMode

    var body: some View {
        VStack(spacing: 12) {
            Label("Front Gate", systemImage: "door.garage.closed")
                .font(.headline)
                .frame(maxWidth: .infinity, alignment: .leading)

            Spacer(minLength: 0)

            if !isConfigured {
                Text("Open Gate Opener to connect your bridge.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
            } else if renderingMode == .fullColor {
                openButton.buttonStyle(.borderedProminent)
            } else {
                // Vibrant (macOS desktop behind windows) and accented modes drop color, so a
                // prominent button's white label disappears into its fill. Use a translucent fill.
                openButton.buttonStyle(.bordered)
            }
        }
    }

    private var openButton: some View {
        Button(intent: OpenGateIntent()) {
            Label("Open / Close", systemImage: "door.garage.open")
                .font(.subheadline)
                .lineLimit(1)
                .minimumScaleFactor(0.8)
                .frame(maxWidth: .infinity)
                .invalidatableContent()
        }
    }
}
