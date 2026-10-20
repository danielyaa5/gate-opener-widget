import Foundation
import Security

/// The bridge this device talks to. Entered in the app, never compiled in, so one
/// signed build can be shared and every person keeps their own API secret.
struct GateConnection: Codable, Equatable, Sendable {
    var bridgeURL: URL
    var secret: String

    static let minimumSecretLength = 32
}

enum GateSettingsError: LocalizedError {
    case invalidURL
    case shortSecret
    case invalidSetupCode
    case keychain(OSStatus)

    var errorDescription: String? {
        switch self {
        case .invalidURL:
            "Enter the bridge origin, such as https://your-mac.your-tailnet.ts.net, without a path."
        case .shortSecret:
            "The API secret must be at least \(GateConnection.minimumSecretLength) characters."
        case .invalidSetupCode:
            "That setup code is not valid. Copy it again from scripts/setup_code.py."
        case let .keychain(status):
            "Could not save to the Keychain (\(status))."
        }
    }
}

enum GateSettings {
    /// Setup codes are `gate1:` followed by base64url JSON `{"url": ..., "secret": ...}`,
    /// printed by scripts/setup_code.py on the bridge Mac.
    static let setupCodePrefix = "gate1:"

    private static let service = "GateOpener.bridge"
    private static let account = "default"
    private static let fallbackKey = "gateConnectionV1"

    /// The app group shared by the app and its widget, from the GateAppGroup Info.plist key.
    private static var appGroup: String? {
        guard let group = Bundle.main.object(forInfoDictionaryKey: "GateAppGroup") as? String,
              !group.isEmpty, !group.contains("$(") else { return nil }
        return group
    }

    private static var keychainGroup: String? {
        guard let group = Bundle.main.object(forInfoDictionaryKey: "GateKeychainAccessGroup") as? String,
              !group.isEmpty, !group.contains("$(") else { return appGroup }
        return group
    }

    static func load() -> GateConnection? {
        if let data = keychainData(), let connection = try? JSONDecoder().decode(GateConnection.self, from: data) {
            return connection
        }
        // Preserve settings when an older local build gains an app group or
        // proper signing. Move its fallback into the Keychain when possible.
        guard let data = fallbackDefaults?.data(forKey: fallbackKey)
                ?? UserDefaults.standard.data(forKey: fallbackKey),
              let connection = try? JSONDecoder().decode(GateConnection.self, from: data) else { return nil }
        let status = saveToKeychain(data)
        if status == errSecSuccess {
            fallbackDefaults?.removeObject(forKey: fallbackKey)
            UserDefaults.standard.removeObject(forKey: fallbackKey)
        } else if status == errSecMissingEntitlement {
            fallbackDefaults?.set(data, forKey: fallbackKey)
        }
        return connection
    }

    static func save(_ connection: GateConnection) throws {
        let data = try JSONEncoder().encode(connection)
        let status = saveToKeychain(data)
        if status == errSecSuccess {
            fallbackDefaults?.removeObject(forKey: fallbackKey)
            return
        }
        // Builds signed without a team (local "Sign to Run Locally" builds) have no
        // application identifier, so the data protection keychain refuses them.
        // Keep those builds usable by storing the connection in the sandboxed group container.
        guard status == errSecMissingEntitlement, let defaults = fallbackDefaults else {
            throw GateSettingsError.keychain(status)
        }
        defaults.set(data, forKey: fallbackKey)
    }

    static func clear() {
        SecItemDelete(baseQuery() as CFDictionary)
        fallbackDefaults?.removeObject(forKey: fallbackKey)
    }

    static func connection(urlString: String, secret: String) throws -> GateConnection {
        let trimmed = urlString.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let components = URLComponents(string: trimmed),
              let scheme = components.scheme?.lowercased(), ["http", "https"].contains(scheme),
              let host = components.host, !host.isEmpty,
              components.user == nil, components.password == nil,
              components.query == nil, components.fragment == nil,
              ["", "/"].contains(components.path),
              let url = URL(string: "\(scheme)://\(host)\(components.port.map { ":\($0)" } ?? "")")
        else {
            throw GateSettingsError.invalidURL
        }
        let secret = secret.trimmingCharacters(in: .whitespacesAndNewlines)
        guard secret.count >= GateConnection.minimumSecretLength else {
            throw GateSettingsError.shortSecret
        }
        return GateConnection(bridgeURL: url, secret: secret)
    }

    static func connection(setupCode: String) throws -> GateConnection {
        let code = setupCode.trimmingCharacters(in: .whitespacesAndNewlines)
        guard code.hasPrefix(setupCodePrefix) else { throw GateSettingsError.invalidSetupCode }
        var encoded = String(code.dropFirst(setupCodePrefix.count))
            .replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/")
        encoded += String(repeating: "=", count: (4 - encoded.count % 4) % 4)
        guard let data = Data(base64Encoded: encoded),
              let payload = try? JSONDecoder().decode(SetupPayload.self, from: data)
        else {
            throw GateSettingsError.invalidSetupCode
        }
        return try connection(urlString: payload.url, secret: payload.secret)
    }

    private struct SetupPayload: Decodable {
        let url: String
        let secret: String
    }

    private static var fallbackDefaults: UserDefaults? {
        appGroup.flatMap(UserDefaults.init(suiteName:)) ?? .standard
    }

    private static func baseQuery(account itemAccount: String = account) -> [String: Any] {
        var query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: itemAccount,
            kSecUseDataProtectionKeychain as String: true,
        ]
        if let keychainGroup {
            query[kSecAttrAccessGroup as String] = keychainGroup
        }
        return query
    }

    private static func keychainData() -> Data? {
        sharedData(account: account)
    }

    static func sharedData(account: String) -> Data? {
        var query = baseQuery(account: account)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess else { return nil }
        return result as? Data
    }

    private static func saveToKeychain(_ data: Data) -> OSStatus {
        saveSharedData(data, account: account)
    }

    static func saveSharedData(_ data: Data, account: String) -> OSStatus {
        let attributes: [String: Any] = [
            kSecValueData as String: data,
            // The Lock Screen control may run while the phone is locked, after first unlock.
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]
        let query = baseQuery(account: account)
        let status = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        guard status == errSecItemNotFound else { return status }
        return SecItemAdd(query.merging(attributes) { $1 } as CFDictionary, nil)
    }
}
