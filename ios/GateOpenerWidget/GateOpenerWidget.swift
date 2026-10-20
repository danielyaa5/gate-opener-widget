import SwiftUI
import WidgetKit

struct GateEntry: TimelineEntry, Sendable {
    let date: Date
    var isConfigured = GateSettings.load() != nil
    var feedback = GateActionFeedback.load()
    var position = GatePositionStore.load()
}

// WidgetKit's callback predates Swift concurrency. Only the task below owns
// and calls it, once, after the read-only request has finished.
private final class GateTimelineDelivery: @unchecked Sendable {
    let complete: (Timeline<GateEntry>) -> Void
    init(_ complete: @escaping (Timeline<GateEntry>) -> Void) { self.complete = complete }
}

struct GateTimelineProvider: TimelineProvider {
    func placeholder(in context: Context) -> GateEntry {
        GateEntry(date: .now)
    }

    func getSnapshot(in context: Context, completion: @escaping (GateEntry) -> Void) {
        completion(GateEntry(date: .now))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<GateEntry>) -> Void) {
        let delivery = GateTimelineDelivery(completion)
        let isPreview = context.isPreview
        Task {
            let recent = GatePositionStore.load()
            if !isPreview, GateSettings.load() != nil,
               recent == nil || Date.now.timeIntervalSince1970 - (recent?.checkedAt ?? 0) > 10 {
                do {
                    let observation = try await GateAPIClient.configured().gatePosition()
                    GatePositionStore.save(observation)
                } catch {
                    // A previously observed state still expires on its original
                    // timestamp when the phone cannot reach the bridge.
                }
            }
            let entry = GateEntry(date: .now)
            // A request, not a guaranteed cadence: WidgetKit applies its budget.
            let nextRefresh = Date.now.addingTimeInterval(60)
            var entries = [entry]
            if let observed = entry.position?.observedDate {
                let expiry = observed.addingTimeInterval(GatePositionObservation.maximumDisplayAge + 1)
                if expiry > .now { entries.append(GateEntry(date: expiry)) }
            }
            if let feedback = entry.feedback, feedback.date.addingTimeInterval(91) > .now {
                entries.append(GateEntry(date: feedback.date.addingTimeInterval(91)))
            }
            delivery.complete(Timeline(entries: entries.sorted { $0.date < $1.date }, policy: .after(nextRefresh)))
        }
    }
}

struct GateOpenerWidget: Widget {
    let kind = "GateOpenerWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: GateTimelineProvider()) { entry in
            GateWidgetView(isConfigured: entry.isConfigured, feedback: entry.feedback, position: entry.position, date: entry.date)
                .containerBackground(.fill.tertiary, for: .widget)
        }
        .configurationDisplayName("Gate Opener")
        .description("Check the gate camera and send an open/close pulse.")
        .supportedFamilies([.systemSmall])
    }
}

struct GateWidgetView: View {
    var isConfigured = true
    var feedback: GateActionFeedback?
    var position: GatePositionObservation?
    var date = Date.now
    @Environment(\.widgetRenderingMode) private var renderingMode

    private var observation: GatePositionObservation { position ?? .unknown() }
    private var state: String { observation.currentState(at: date) }
    private var statusColor: Color {
        guard renderingMode == .fullColor else { return .primary }
        return state == "closed" ? .green : state == "open" ? .orange : .secondary
    }
    private var statusTitle: String {
        state == "open" ? "Open" : state == "closed" ? "Closed" : observation.title(at: date)
    }
    private var recentFeedback: GateActionFeedback? {
        guard let feedback, date.timeIntervalSince(feedback.date) < 90 else { return nil }
        return feedback
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(spacing: 4) {
                Text("Front Gate")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                Spacer(minLength: 0)
                if isConfigured {
                    Button(intent: RefreshGateStatusIntent()) {
                        Image(systemName: "arrow.clockwise")
                            .font(.caption.weight(.semibold))
                            .frame(width: 28, height: 28)
                            .contentShape(Circle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Refresh gate status without moving the gate")
                }
            }

            if isConfigured {
                HStack(spacing: 7) {
                    Image(systemName: observation.symbol(at: date))
                        .font(.title2)
                        .symbolRenderingMode(.hierarchical)
                        .foregroundStyle(statusColor)
                        .accessibilityHidden(true)
                    Text(statusTitle)
                        .font(state == "unknown" ? .subheadline.weight(.semibold) : .title2.weight(.semibold))
                        .lineLimit(2)
                        .minimumScaleFactor(0.85)
                        .accessibilityIdentifier("gatePosition")
                }

                Group {
                    if let feedback = recentFeedback {
                        Text(feedback.outcome == .accepted ? "Command sent" : feedback.outcome == .sending ? "Sending…" : "Command failed")
                            .foregroundStyle(feedback.outcome == .failed ? Color.orange : Color.secondary)
                    } else if let observed = position?.observedDate {
                        Text("Seen \(observed, style: .relative) ago")
                            .foregroundStyle(.secondary)
                    } else {
                        Text("Tap refresh to check")
                            .foregroundStyle(.secondary)
                    }
                }
                .font(.caption2)
                .lineLimit(1)
            }

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
            Label(feedback?.isSending(at: date) == true ? "Sending…" : observation.actionTitle(at: date), systemImage: observation.actionSymbol(at: date))
                .font(.caption.weight(.semibold))
                .lineLimit(1)
                .minimumScaleFactor(0.8)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 3)
                .invalidatableContent()
        }
        .disabled(feedback?.isSending(at: date) == true)
    }
}
