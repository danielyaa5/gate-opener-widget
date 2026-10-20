import Foundation
import Security

/// Last button result shared with the widget. Never contains connection credentials.
struct GateActionFeedback: Codable, Sendable {
    enum Outcome: String, Codable, Sendable {
        case sending, accepted, failed
    }

    let date: Date
    let outcome: Outcome
    let message: String

    var isSending: Bool {
        isSending(at: .now)
    }

    func isSending(at referenceDate: Date) -> Bool {
        outcome == .sending && referenceDate.timeIntervalSince(date) < 90
    }

    var displayMessage: String {
        if outcome == .sending && !isSending {
            return "No result received. Check the gate before trying again."
        }
        return message
    }

    private static let key = "gateActionFeedbackV1"

    static func load() -> GateActionFeedback? {
        guard let data = GateSettings.sharedData(account: key) ?? defaults.data(forKey: key) else { return nil }
        return try? JSONDecoder().decode(Self.self, from: data)
    }

    @discardableResult
    static func save(_ outcome: Outcome, message: String) -> GateActionFeedback {
        let feedback = Self(date: .now, outcome: outcome, message: message)
        if let data = try? JSONEncoder().encode(feedback) {
            if GateSettings.saveSharedData(data, account: key) == errSecSuccess {
                defaults.removeObject(forKey: key)
            } else {
                defaults.set(data, forKey: key)
            }
        }
        return feedback
    }

    private static var defaults: UserDefaults {
        if let group = Bundle.main.object(forInfoDictionaryKey: "GateAppGroup") as? String,
           !group.isEmpty, !group.contains("$("),
           let shared = UserDefaults(suiteName: group) {
            return shared
        }
        return .standard
    }
}
