import CryptoKit
import Foundation
import OSLog
import Security

enum GateAPIError: LocalizedError {
    case notConfigured
    case invalidResponse
    case rejected(statusCode: Int, message: String)

    var errorDescription: String? {
        switch self {
        case .notConfigured:
            "Open Gate Opener and enter your bridge URL and API secret."
        case .invalidResponse:
            "The gate bridge returned an invalid response."
        case let .rejected(statusCode, message):
            "Gate request failed (\(statusCode)): \(message)"
        }
    }
}

struct GateAPIClient: Sendable {
    let baseURL: URL
    let secret: String

    private static let logger = Logger(
        subsystem: "com.danielyakobian.GateOpener",
        category: "GateAPI"
    )

    private static let session = URLSession(
        configuration: .ephemeral,
        delegate: GateTrustDelegate.shared,
        delegateQueue: nil
    )

    /// The client for the bridge saved in the app's settings.
    static func configured() throws -> GateAPIClient {
        guard let connection = GateSettings.load() else { throw GateAPIError.notConfigured }
        return GateAPIClient(baseURL: connection.bridgeURL, secret: connection.secret)
    }

    func openGate() async throws {
        let requestID = makeRequestID()
        Self.logger.notice("Open started request=\(requestID, privacy: .public)")
        await record(operation: "open", outcome: "started", requestID: requestID)

        // A TLS handshake failure means HTTP never reached the bridge, so retrying
        // cannot duplicate a gate pulse. Do not retry timeouts or lost connections:
        // those can happen after the bridge has already accepted the request.
        let retryDelays: [UInt64] = [2, 6, 12]
        var retryIndex = 0
        while true {
            do {
                let request = signedRequest(path: "/v1/open", method: "POST", requestID: requestID)
                let (data, response) = try await Self.session.data(for: request)
                try validate(data: data, response: response)
                guard let result = try? JSONDecoder().decode(OpenResponse.self, from: data),
                      result.success,
                      result.result == nil || result.result == "command_accepted" else {
                    throw GateAPIError.invalidResponse
                }
                Self.logger.notice("Open succeeded request=\(requestID, privacy: .public)")
                await record(operation: "open", outcome: "succeeded", requestID: requestID)
                await uploadPendingDiagnostics()
                return
            } catch {
                guard isSecureConnectionFailure(error), retryIndex < retryDelays.count else {
                    await recordFailure(operation: "open", requestID: requestID, error: error)
                    if error is GateAPIError {
                        await uploadPendingDiagnostics()
                    }
                    throw error
                }

                let delay = retryDelays[retryIndex]
                retryIndex += 1
                let nsError = error as NSError
                Self.logger.warning(
                    "Open TLS retry request=\(requestID, privacy: .public) attempt=\(retryIndex) delay=\(delay)s"
                )
                await record(
                    operation: "open",
                    outcome: "retrying",
                    requestID: requestID,
                    errorDomain: nsError.domain,
                    errorCode: nsError.code
                )
                do {
                    try await Task.sleep(nanoseconds: delay * 1_000_000_000)
                } catch {
                    await recordFailure(operation: "open", requestID: requestID, error: error)
                    throw error
                }
            }
        }
    }

    func isOnline() async throws -> Bool {
        let requestID = makeRequestID()
        Self.logger.notice("Status check started request=\(requestID, privacy: .public)")
        await record(operation: "status", outcome: "started", requestID: requestID)

        // Status is read-only, so retry transient connection setup failures.
        // This also warms DNS/TLS for a widget's subsequent one-shot open call.
        let retryDelays: [UInt64] = [1, 2]
        var retryIndex = 0
        while true {
            do {
                let request = signedRequest(
                    path: "/v1/status",
                    method: "GET",
                    requestID: requestID,
                    timeout: 4
                )
                let (data, response) = try await Self.session.data(for: request)
                try validate(data: data, response: response)
                guard let status = try? JSONDecoder().decode(StatusResponse.self, from: data),
                      status.online else {
                    throw GateAPIError.invalidResponse
                }
                Self.logger.notice("Status check succeeded request=\(requestID, privacy: .public)")
                await record(operation: "status", outcome: "succeeded", requestID: requestID)
                await uploadPendingDiagnostics()
                return true
            } catch {
                guard isTransientConnectionFailure(error), retryIndex < retryDelays.count else {
                    await recordFailure(operation: "status", requestID: requestID, error: error)
                    if error is GateAPIError {
                        await uploadPendingDiagnostics()
                    }
                    throw error
                }

                let delay = retryDelays[retryIndex]
                retryIndex += 1
                let nsError = error as NSError
                Self.logger.warning(
                    "Status retry request=\(requestID, privacy: .public) attempt=\(retryIndex) delay=\(delay)s"
                )
                await record(
                    operation: "status",
                    outcome: "retrying",
                    requestID: requestID,
                    errorDomain: nsError.domain,
                    errorCode: nsError.code
                )
                try await Task.sleep(nanoseconds: delay * 1_000_000_000)
            }
        }
    }

    func gatePosition() async throws -> GatePositionObservation {
        let requestID = makeRequestID()
        await record(operation: "position", outcome: "started", requestID: requestID)
        do {
            let request = signedRequest(path: "/v1/position", method: "GET", requestID: requestID, timeout: 28)
            let (data, response) = try await Self.session.data(for: request)
            try validate(data: data, response: response)
            let observation = try JSONDecoder().decode(GatePositionObservation.self, from: data)
            await record(operation: "position", outcome: "succeeded", requestID: requestID)
            return observation
        } catch {
            await recordFailure(operation: "position", requestID: requestID, error: error)
            throw error
        }
    }

    func cameraPreview() async throws -> GateCameraPreview {
        let request = signedRequest(path: "/v1/camera", method: "GET", requestID: makeRequestID(), timeout: 12)
        let (data, response) = try await Self.session.data(for: request)
        try validate(data: data, response: response)
        guard data.count <= 7_100_000 else { throw GateAPIError.invalidResponse }
        return try JSONDecoder().decode(GateCameraPreview.self, from: data)
    }

    private func signedRequest(
        path: String,
        method: String,
        requestID: String,
        body: Data = Data(),
        timeout: TimeInterval = 8
    ) -> URLRequest {
        let timestamp = String(Date().timeIntervalSince1970)
        let nonce = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        let bodyDigest = SHA256.hash(data: body).hexString
        let payload = "\(timestamp)\n\(nonce)\n\(method)\n\(path)\n\(bodyDigest)"
        let signature = HMAC<SHA256>.authenticationCode(
            for: Data(payload.utf8),
            using: SymmetricKey(data: Data(secret.utf8))
        ).hexString

        let endpoint = baseURL.appendingPathComponent(String(path.dropFirst()))
        var request = URLRequest(url: endpoint)
        request.httpMethod = method
        request.httpBody = body
        request.timeoutInterval = timeout
        request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        request.setValue(timestamp, forHTTPHeaderField: "X-Gate-Timestamp")
        request.setValue(nonce, forHTTPHeaderField: "X-Gate-Nonce")
        request.setValue(signature, forHTTPHeaderField: "X-Gate-Signature")
        request.setValue(requestID, forHTTPHeaderField: "X-Gate-Request-ID")
        request.setValue("no-cache", forHTTPHeaderField: "Cache-Control")
        return request
    }

    private func makeRequestID() -> String {
        String(UUID().uuidString.replacingOccurrences(of: "-", with: "").prefix(12)).lowercased()
    }

    private func isSecureConnectionFailure(_ error: Error) -> Bool {
        (error as? URLError)?.code == .secureConnectionFailed
    }

    private func isTransientConnectionFailure(_ error: Error) -> Bool {
        guard let code = (error as? URLError)?.code else { return false }
        return [
            .secureConnectionFailed,
            .timedOut,
            .cannotFindHost,
            .cannotConnectToHost,
            .networkConnectionLost,
            .dnsLookupFailed,
        ].contains(code)
    }

    private func record(
        operation: String,
        outcome: String,
        requestID: String,
        errorDomain: String? = nil,
        errorCode: Int? = nil,
        httpStatus: Int? = nil
    ) async {
        let event = GateDiagnosticEvent(
            id: UUID().uuidString.lowercased(),
            timestamp: Date().timeIntervalSince1970,
            component: Bundle.main.bundleIdentifier ?? "unknown",
            operation: operation,
            outcome: outcome,
            requestID: requestID,
            errorDomain: errorDomain,
            errorCode: errorCode,
            httpStatus: httpStatus
        )
        await GateDiagnosticStore.shared.append(event)
    }

    private func recordFailure(operation: String, requestID: String, error: Error) async {
        if case let GateAPIError.rejected(statusCode, _) = error {
            Self.logger.error(
                "\(operation, privacy: .public) rejected request=\(requestID, privacy: .public) status=\(statusCode)"
            )
            await record(
                operation: operation,
                outcome: "rejected",
                requestID: requestID,
                httpStatus: statusCode
            )
            return
        }

        let nsError = error as NSError
        Self.logger.error(
            "\(operation, privacy: .public) failed request=\(requestID, privacy: .public) domain=\(nsError.domain, privacy: .public) code=\(nsError.code)"
        )
        await record(
            operation: operation,
            outcome: "failed",
            requestID: requestID,
            errorDomain: nsError.domain,
            errorCode: nsError.code
        )
    }

    private func uploadPendingDiagnostics() async {
        let events = await GateDiagnosticStore.shared.batch()
        guard !events.isEmpty else { return }

        do {
            let body = try JSONEncoder().encode(GateDiagnosticPayload(events: events))
            let uploadID = makeRequestID()
            let request = signedRequest(
                path: "/v1/client-log",
                method: "POST",
                requestID: uploadID,
                body: body,
                timeout: 3
            )
            let (data, response) = try await Self.session.data(for: request)
            try validate(data: data, response: response)
            await GateDiagnosticStore.shared.remove(ids: Set(events.map(\.id)))
            Self.logger.notice(
                "Uploaded \(events.count) diagnostic events request=\(uploadID, privacy: .public)"
            )
        } catch {
            let nsError = error as NSError
            Self.logger.warning(
                "Diagnostic upload deferred domain=\(nsError.domain, privacy: .public) code=\(nsError.code)"
            )
        }
    }

    private func validate(data: Data, response: URLResponse) throws {
        guard let httpResponse = response as? HTTPURLResponse else {
            throw GateAPIError.invalidResponse
        }
        guard (200..<300).contains(httpResponse.statusCode) else {
            let payload = try? JSONDecoder().decode(ErrorPayload.self, from: data)
            throw GateAPIError.rejected(
                statusCode: httpResponse.statusCode,
                message: payload?.error ?? "Unknown error"
            )
        }
    }
}

/// iOS 27.0 does not yet build Let’s Encrypt's new Root YE chain even though
/// the chain is cross-signed by the system-trusted ISRG Root X2. Keep the
/// workaround scoped to this app: add the official Root YE certificate as an
/// additional URLSession anchor while retaining normal hostname and chain
/// validation. This does not modify the phone's system trust settings.
private final class GateTrustDelegate: NSObject, URLSessionDelegate, @unchecked Sendable {
    static let shared = GateTrustDelegate()

    private static let rootYEBase64 =
        "MIIB2TCCAWCgAwIBAgIRAKQCa6LvbHwg1AR+XmWmk4AwCgYIKoZIzj0EAwMwLjELMAkGA1UEBhMCVVMxDTALBgNVBAoTBElTUkcxEDAOBgNVBAMTB1Jvb3QgWUUwHhcNMjUwOTAzMDAwMDAwWhcNNDUwOTAyMjM1OTU5WjAuMQswCQYDVQQGEwJVUzENMAsGA1UEChMESVNSRzEQMA4GA1UEAxMHUm9vdCBZRTB2MBAGByqGSM49AgEGBSuBBAAiA2IABDwS/6vhrcVqcbBo+wgdI3fwn9x7DNJJOY/lTOti0vkwuRN87RhEhTH17E7XyFjWsPYhIPt/wzOqxTd2b+4ZJNy9ID04YywF9U5zasDVyGSNErVNtz8uSGh5izW87j77GaNCMEAwDgYDVR0PAQH/BAQDAgEGMA8GA1UdEwEB/wQFMAMBAf8wHQYDVR0OBBYEFKPIJlqOoUzQNWP8myPIOq5W809WMAoGCCqGSM49BAMDA2cAMGQCMHhMr8N9LdL1VQKs9BdV81r76eXRB6mtjuNjzk6/lBsPNToWLTDzGYgtQKO1jl63uAIwGV7monyF377c+MM1oqVNs17sgu7F9YKZwgLmVbeOMDbKAXHtKMDLbiGllCcs8f47"

    // Tailscale currently serves this official Root YE certificate cross-signed
    // by ISRG Root X2. iOS 27 does not consistently connect it to Root YE's
    // self-signed form, so include the exact cross-signed CA as an anchor too.
    private static let rootYECrossSignedBase64 =
        "MIICpjCCAiugAwIBAgIRAIchZfw0tuX7qK3Vs3BftTowCgYIKoZIzj0EAwMwTzELMAkGA1UEBhMCVVMxKTAnBgNVBAoTIEludGVybmV0IFNlY3VyaXR5IFJlc2VhcmNoIEdyb3VwMRUwEwYDVQQDEwxJU1JHIFJvb3QgWDIwHhcNMjYwNTEzMDAwMDAwWhcNMzIwOTAyMjM1OTU5WjAuMQswCQYDVQQGEwJVUzENMAsGA1UEChMESVNSRzEQMA4GA1UEAxMHUm9vdCBZRTB2MBAGByqGSM49AgEGBSuBBAAiA2IABDwS/6vhrcVqcbBo+wgdI3fwn9x7DNJJOY/lTOti0vkwuRN87RhEhTH17E7XyFjWsPYhIPt/wzOqxTd2b+4ZJNy9ID04YywF9U5zasDVyGSNErVNtz8uSGh5izW87j77GaOB6zCB6DAOBgNVHQ8BAf8EBAMCAQYwEwYDVR0lBAwwCgYIKwYBBQUHAwEwDwYDVR0TAQH/BAUwAwEB/zAdBgNVHQ4EFgQUo8gmWo6hTNA1Y/ybI8g6rlbzT1YwHwYDVR0jBBgwFoAUfEKWrt5LSDv6kviejM9ti6lyN5UwMgYIKwYBBQUHAQEEJjAkMCIGCCsGAQUFBzAChhZodHRwOi8veDIuaS5sZW5jci5vcmcvMBMGA1UdIAQMMAowCAYGZ4EMAQIBMCcGA1UdHwQgMB4wHKAaoBiGFmh0dHA6Ly94Mi5jLmxlbmNyLm9yZy8wCgYIKoZIzj0EAwMDaQAwZgIxAMU19WCtmxVND8UHBZRoma49Z7jPs64Dma0eTu1OChVbB/2J7GV3nvYKAx54uk1G9QIxAO0miLVJu8PLNiXXXkiE/gsK3CTRTF/aeo4bMX42Zw40csRU6AC26hSW1/IWaas6dg=="

    func urlSession(
        _ session: URLSession,
        didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping @Sendable (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              let serverTrust = challenge.protectionSpace.serverTrust,
              let certificateData = Data(base64Encoded: Self.rootYEBase64),
              let rootYE = SecCertificateCreateWithData(nil, certificateData as CFData),
              let crossSignedData = Data(base64Encoded: Self.rootYECrossSignedBase64),
              let crossSignedRootYE = SecCertificateCreateWithData(nil, crossSignedData as CFData)
        else {
            completionHandler(.performDefaultHandling, nil)
            return
        }

        let hostname = challenge.protectionSpace.host as CFString
        SecTrustSetPolicies(serverTrust, SecPolicyCreateSSL(true, hostname))
        SecTrustSetAnchorCertificates(serverTrust, [rootYE, crossSignedRootYE] as CFArray)
        SecTrustSetAnchorCertificatesOnly(serverTrust, false)

        if SecTrustEvaluateWithError(serverTrust, nil) {
            completionHandler(.useCredential, URLCredential(trust: serverTrust))
        } else {
            completionHandler(.cancelAuthenticationChallenge, nil)
        }
    }
}

private struct GateDiagnosticPayload: Codable, Sendable {
    let events: [GateDiagnosticEvent]
}

private struct GateDiagnosticEvent: Codable, Sendable {
    let id: String
    let timestamp: TimeInterval
    let component: String
    let operation: String
    let outcome: String
    let requestID: String
    let errorDomain: String?
    let errorCode: Int?
    let httpStatus: Int?
}

private actor GateDiagnosticStore {
    static let shared = GateDiagnosticStore()

    private let defaultsKey = "gateDiagnosticEventsV1"
    private let maximumEvents = 100

    func append(_ event: GateDiagnosticEvent) {
        var events = load()
        events.append(event)
        if events.count > maximumEvents {
            events = Array(events.suffix(maximumEvents))
        }
        save(events)
    }

    func batch(limit: Int = 25) -> [GateDiagnosticEvent] {
        Array(load().prefix(limit))
    }

    func remove(ids: Set<String>) {
        save(load().filter { !ids.contains($0.id) })
    }

    private func load() -> [GateDiagnosticEvent] {
        guard let data = UserDefaults.standard.data(forKey: defaultsKey) else { return [] }
        return (try? JSONDecoder().decode([GateDiagnosticEvent].self, from: data)) ?? []
    }

    private func save(_ events: [GateDiagnosticEvent]) {
        guard let data = try? JSONEncoder().encode(events) else { return }
        UserDefaults.standard.set(data, forKey: defaultsKey)
    }
}

private struct ErrorPayload: Decodable {
    let error: String
}

private struct OpenResponse: Decodable {
    let success: Bool
    let result: String?
}

private struct StatusResponse: Decodable {
    let online: Bool
}

private extension Digest {
    var hexString: String {
        map { String(format: "%02x", $0) }.joined()
    }
}
private extension MessageAuthenticationCode {
    var hexString: String {
        map { String(format: "%02x", $0) }.joined()
    }
}
