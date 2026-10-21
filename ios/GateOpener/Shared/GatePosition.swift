import Foundation
import Security

struct GateLearningStatus: Decodable, Sendable {
    let enabled: Bool
    let candidate: GateLearningCandidate?
}

struct GateLearningCandidate: Decodable, Identifiable, Sendable {
    let id: String
    let capturedAt: TimeInterval
    let imageBase64: String
    let cameraName: String

    enum CodingKeys: String, CodingKey {
        case id
        case capturedAt = "captured_at"
        case imageBase64 = "image_base64"
        case cameraName = "camera_name"
    }

    var imageData: Data? {
        guard capturedAt.isFinite, imageBase64.utf8.count <= 7_000_000,
              let data = Data(base64Encoded: imageBase64), data.count <= 5_000_000,
              data.starts(with: [0xff, 0xd8]) else { return nil }
        return data
    }
}

/// Foreground-only camera preview. Images are never persisted in widget settings.
struct GateCameraPreview: Decodable, Sendable {
    let cameraName: String
    let ringURLString: String
    let capturedAt: TimeInterval?
    let imageBase64: String?

    enum CodingKeys: String, CodingKey {
        case cameraName = "camera_name"
        case ringURLString = "ring_url"
        case capturedAt = "captured_at"
        case imageBase64 = "image_base64"
    }

    var ringURL: URL? {
        guard let url = URL(string: ringURLString), url.scheme == "https",
              url.host == "account.ring.com", url.user == nil, url.password == nil,
              url.port == nil, url.path == "/account/dashboard" else { return nil }
        return url
    }

    func capturedDate(at date: Date = .now) -> Date? {
        guard let capturedAt, capturedAt.isFinite,
              (-5...86400).contains(date.timeIntervalSince1970 - capturedAt) else { return nil }
        return Date(timeIntervalSince1970: capturedAt)
    }

    func imageData(at date: Date = .now) -> Data? {
        guard capturedDate(at: date) != nil, let imageBase64, imageBase64.utf8.count <= 7_000_000,
              let data = Data(base64Encoded: imageBase64), data.count <= 5_000_000,
              data.starts(with: [0xff, 0xd8]) else { return nil }
        return data
    }
}

/// An observation of the gate, independent of the relay command's result.
struct GatePositionObservation: Codable, Sendable {
    let state: String
    let observedAt: TimeInterval?
    let checkedAt: TimeInterval
    let reason: String

    enum CodingKeys: String, CodingKey {
        case state, reason
        case observedAt = "observed_at"
        case checkedAt = "checked_at"
    }

    static let maximumDisplayAge: TimeInterval = 300

    static func unknown(_ reason: String = "not_checked") -> Self {
        Self(state: "unknown", observedAt: nil, checkedAt: Date.now.timeIntervalSince1970, reason: reason)
    }

    var observedDate: Date? {
        observedAt.map(Date.init(timeIntervalSince1970:))
    }

    func currentState(at date: Date = .now) -> String {
        guard ["open", "closed"].contains(state), reason == "matched",
              let observedAt, observedAt.isFinite, checkedAt.isFinite,
              (-5...Self.maximumDisplayAge).contains(date.timeIntervalSince1970 - observedAt),
              (-5...Self.maximumDisplayAge).contains(date.timeIntervalSince1970 - checkedAt)
        else { return "unknown" }
        return state
    }

    func title(at date: Date = .now) -> String {
        switch currentState(at: date) {
        case "open": return "Gate open"
        case "closed": return "Gate closed"
        default:
            if state != "unknown" { return "Status needs refresh" }
            switch reason {
            case "needs_day_references": return "Day view not set up"
            case "needs_night_references": return "Night view not set up"
            case "not_configured": return "Camera not set up"
            case "movement": return "Gate may be moving"
            case "camera_unavailable": return "Camera unavailable"
            case "stale_snapshot": return "Waiting for new image"
            case "unclear": return "Gate position unclear"
            default: return "Gate status not checked"
            }
        }
    }

    func actionTitle(at date: Date = .now) -> String {
        switch currentState(at: date) {
        case "closed": return "Open Gate"
        case "open": return "Close Gate"
        default: return "Open / Close"
        }
    }

    func actionSymbol(at date: Date = .now) -> String {
        switch currentState(at: date) {
        case "closed": return "door.garage.open"
        case "open": return "door.garage.closed"
        default: return "hand.tap"
        }
    }

    func confirmationTitle(at date: Date = .now) -> String {
        switch currentState(at: date) {
        case "closed": return "Open the front gate?"
        case "open": return "Close the front gate?"
        default: return "Open or close the front gate?"
        }
    }

    func symbol(at date: Date = .now) -> String {
        switch currentState(at: date) {
        case "open": return "door.garage.open"
        case "closed": return "door.garage.closed"
        default: return "questionmark.circle"
        }
    }
}

enum GatePositionStore {
    private static let key = "gatePositionObservationV1"

    static func load() -> GatePositionObservation? {
        guard let data = GateSettings.sharedData(account: key) else { return nil }
        return try? JSONDecoder().decode(GatePositionObservation.self, from: data)
    }

    @discardableResult
    static func save(_ observation: GatePositionObservation) -> GatePositionObservation {
        if let data = try? JSONEncoder().encode(observation) {
            _ = GateSettings.saveSharedData(data, account: key)
        }
        return observation
    }
}
