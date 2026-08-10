import XCTest
@testable import Powermon

/// Serves canned responses so the client's status handling can be tested
/// without a server.
final class StubURLProtocol: URLProtocol {
    nonisolated(unsafe) static var status = 200
    nonisolated(unsafe) static var body = Data()
    nonisolated(unsafe) static var lastRequest: URLRequest?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func stopLoading() {}

    override func startLoading() {
        Self.lastRequest = request
        let response = HTTPURLResponse(
            url: request.url!, statusCode: Self.status, httpVersion: nil, headerFields: nil
        )!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Self.body)
        client?.urlProtocolDidFinishLoading(self)
    }
}

final class PowermonClientTests: XCTestCase {
    private let baseURL = URL(string: "http://marzmesas-trainbox:8787")!

    private func makeClient(token: String?) -> PowermonClient {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubURLProtocol.self]
        return PowermonClient(baseURL: baseURL, token: token, session: URLSession(configuration: config))
    }

    override func setUp() {
        super.setUp()
        StubURLProtocol.status = 200
        StubURLProtocol.body = Data()
        StubURLProtocol.lastRequest = nil
    }

    /// A 401 body is `{"error": …}`, not a Snapshot. Mapping it to
    /// `.unauthorized` *before* decoding is what makes "token rejected"
    /// distinguishable from "unreachable" — different problems, different fixes.
    func testUnauthorizedIsMappedBeforeDecoding() async {
        StubURLProtocol.status = 401
        StubURLProtocol.body = Data(#"{"error": "unauthorised: token required"}"#.utf8)

        do {
            _ = try await makeClient(token: "wrong").fetchNow()
            XCTFail("expected an error")
        } catch let error as ClientError {
            XCTAssertEqual(error, .unauthorized)
        } catch {
            XCTFail("expected ClientError.unauthorized, got \(error)")
        }
    }

    func testTokenIsSentAsHeader() async throws {
        StubURLProtocol.body = try fixtureData("sample-now")
        _ = try await makeClient(token: "s3cret").fetchNow()

        let sent = try XCTUnwrap(StubURLProtocol.lastRequest)
        XCTAssertEqual(sent.value(forHTTPHeaderField: "X-Powermon-Token"), "s3cret")
        // Never in the URL, where it would end up in logs and history.
        XCTAssertFalse(try XCTUnwrap(sent.url?.absoluteString).contains("s3cret"))
        XCTAssertEqual(sent.url?.path, "/api/now")
    }

    func testNoHeaderWhenTokenIsEmpty() async throws {
        StubURLProtocol.body = try fixtureData("sample-now")
        _ = try await makeClient(token: "").fetchNow()

        let sent = try XCTUnwrap(StubURLProtocol.lastRequest)
        XCTAssertNil(sent.value(forHTTPHeaderField: "X-Powermon-Token"))
    }

    func testServerErrorSurfacesStatusCode() async {
        StubURLProtocol.status = 500
        StubURLProtocol.body = Data(#"{"error": "boom"}"#.utf8)

        do {
            _ = try await makeClient(token: nil).fetchNow()
            XCTFail("expected an error")
        } catch let error as ClientError {
            XCTAssertEqual(error, .badStatus(500))
        } catch {
            XCTFail("expected ClientError.badStatus, got \(error)")
        }
    }

    func testHealthDecodes() async throws {
        StubURLProtocol.body = Data(#"{"ok": true, "last_sample_age_s": 1.31}"#.utf8)
        let health = try await makeClient(token: nil).fetchHealth()

        XCTAssertTrue(health.ok)
        XCTAssertEqual(health.lastSampleAgeS, 1.31, accuracy: 0.001)
        XCTAssertEqual(StubURLProtocol.lastRequest?.url?.path, "/healthz")
    }

    private func fixtureData(_ name: String) throws -> Data {
        let url = try XCTUnwrap(Bundle(for: Self.self).url(forResource: name, withExtension: "json"))
        return try Data(contentsOf: url)
    }
}
