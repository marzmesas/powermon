import Foundation

enum ClientError: LocalizedError, Equatable {
    case unauthorized
    case badStatus(Int)
    case transport(String)
    case decoding(String)

    var errorDescription: String? {
        switch self {
        case .unauthorized:        return "Token rejected"
        case .badStatus(let code): return "Server returned HTTP \(code)"
        case .transport(let why):  return why
        case .decoding(let why):   return "Unexpected response: \(why)"
        }
    }
}

/// One `URLSession` request per call, no shared mutable state.
final class PowermonClient {
    let baseURL: URL
    private let token: String?
    private let session: URLSession

    init(baseURL: URL, token: String?, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.token = token
        self.session = session
    }

    func fetchNow() async throws -> Snapshot { try await get("api/now") }
    func fetchHealth() async throws -> Health { try await get("healthz") }

    private func get<T: Decodable>(_ path: String) async throws -> T {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.timeoutInterval = 5
        request.cachePolicy = .reloadIgnoringLocalCacheData
        if let token, !token.isEmpty {
            request.setValue(token, forHTTPHeaderField: "X-Powermon-Token")
        }

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw ClientError.transport(error.localizedDescription)
        }

        guard let http = response as? HTTPURLResponse else {
            throw ClientError.transport("Not an HTTP response")
        }
        // Map 401 before decoding: the body is {"error": …}, not a Snapshot.
        guard http.statusCode != 401 else { throw ClientError.unauthorized }
        guard (200..<300).contains(http.statusCode) else {
            throw ClientError.badStatus(http.statusCode)
        }

        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        do {
            return try decoder.decode(T.self, from: data)
        } catch {
            throw ClientError.decoding(error.localizedDescription)
        }
    }
}
