import XCTest
@testable import Powermon

/// The state machine from design doc §5.3 and §12. These are the states that
/// decide whether the menu bar shows a number or an em dash, so "it compiles"
/// is not evidence they work.
@MainActor
final class PollerTests: XCTestCase {
    private var suite: String!
    private var defaults: UserDefaults!

    override func setUp() {
        super.setUp()
        // A private domain, so tests never touch the real app's preferences.
        suite = "powermon.tests.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suite)
        defaults.set("http://stub.invalid:8787", forKey: "serverURL")
        defaults.set(2.0, forKey: "refreshInterval")

        StubURLProtocol.status = 200
        StubURLProtocol.body = fixture("sample-now")
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suite)
        super.tearDown()
    }

    private func makePoller(stallThreshold: TimeInterval = 30) -> Poller {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubURLProtocol.self]
        return Poller(
            settings: AppSettings(defaults: defaults),
            session: URLSession(configuration: config),
            stallThreshold: stallThreshold
        )
    }

    private func fixture(_ name: String) -> Data {
        let url = Bundle(for: Self.self).url(forResource: name, withExtension: "json")!
        return try! Data(contentsOf: url)
    }

    /// Polls the state until it matches, rather than sleeping a fixed time.
    private func wait(
        _ poller: Poller,
        for predicate: @escaping (ConnectionState) -> Bool,
        timeout: TimeInterval,
        _ message: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if predicate(poller.state) { return }
            try? await Task.sleep(nanoseconds: 100_000_000)
        }
        XCTFail("timed out waiting for \(message); state was \(poller.state)", file: file, line: line)
    }

    func testReachesLiveAndPublishesASnapshot() async {
        let poller = makePoller()
        await wait(poller, for: { $0 == .live }, timeout: 5, "live")

        XCTAssertEqual(poller.snapshot?.meta.host, "marzmesas-trainbox")
        XCTAssertEqual(poller.snapshot?.power.totalW ?? 0, 104.42, accuracy: 0.01)
    }

    /// 401 is its own state, never "unreachable" — a rejected token and a dead
    /// server need different fixes from the user.
    func testUnauthorizedIsDistinctFromUnreachable() async {
        StubURLProtocol.status = 401
        StubURLProtocol.body = Data(#"{"error": "unauthorised: token required"}"#.utf8)

        let poller = makePoller()
        await wait(poller, for: { $0 == .unauthorized }, timeout: 5, "unauthorized")
        XCTAssertNil(poller.snapshot)
    }

    func testNoServerURLIsReportedNotCrashed() async {
        defaults.set("", forKey: "serverURL")
        let poller = makePoller()

        await wait(poller, for: {
            if case .unreachable = $0 { return true } else { return false }
        }, timeout: 5, "unreachable")
    }

    /// One dropped poll dims the panel; it must not blank the number, and it
    /// must not be mistaken for the server being gone.
    func testASingleFailureGoesStaleNotUnreachable() async {
        let poller = makePoller()
        await wait(poller, for: { $0 == .live }, timeout: 5, "live first")

        StubURLProtocol.status = 500
        StubURLProtocol.body = Data(#"{"error": "boom"}"#.utf8)

        await wait(poller, for: {
            if case .stale = $0 { return true } else { return false }
        }, timeout: 8, "stale after one failure")

        // The last good reading is still there to render, dimmed.
        XCTAssertNotNil(poller.snapshot)
    }

    /// A sustained outage eventually stops claiming the last value is current.
    func testSustainedFailureBecomesUnreachable() async {
        let poller = makePoller()
        await wait(poller, for: { $0 == .live }, timeout: 5, "live first")

        StubURLProtocol.status = 500
        StubURLProtocol.body = Data(#"{"error": "boom"}"#.utf8)

        await wait(poller, for: {
            if case .unreachable = $0 { return true } else { return false }
        }, timeout: 20, "unreachable after sustained failure")

        XCTAssertFalse(poller.state.showsLastValue, "the menu bar must not show a stale number")
    }

    /// The server answering with the same sample forever means the HTTP thread
    /// is alive and the sampler is wedged — /healthz's `ok: false`, detected
    /// without a second request or any clock comparison.
    func testRepeatedIdenticalSampleIsDetectedAsAWedgedSampler() async {
        let poller = makePoller(stallThreshold: 0.5)
        await wait(poller, for: { $0 == .live }, timeout: 5, "live first")

        // Body never changes, so `ts` never advances.
        await wait(poller, for: { $0 == .samplerStalled }, timeout: 10, "samplerStalled")

        // Still shows the number: the box really is drawing that much.
        XCTAssertTrue(poller.state.showsLastValue)
    }

    func testRecoveryAfterAnOutage() async {
        let poller = makePoller()
        await wait(poller, for: { $0 == .live }, timeout: 5, "live first")

        StubURLProtocol.status = 500
        await wait(poller, for: {
            if case .stale = $0 { return true } else { return false }
        }, timeout: 8, "stale")

        StubURLProtocol.status = 200
        StubURLProtocol.body = fixture("sample-now")
        await wait(poller, for: { $0 == .live }, timeout: 20, "live again without a relaunch")
    }

    /// Correcting the token must not sit behind the failure backoff, which
    /// reaches 60 s — the user would think the fix had not worked.
    func testSettingsChangeRetriesImmediately() async {
        StubURLProtocol.status = 401
        StubURLProtocol.body = Data(#"{"error": "unauthorised"}"#.utf8)

        let poller = makePoller()
        await wait(poller, for: { $0 == .unauthorized }, timeout: 5, "unauthorized")

        StubURLProtocol.status = 200
        StubURLProtocol.body = fixture("sample-now")
        NotificationCenter.default.post(name: .powermonSettingsChanged, object: nil)

        // Well inside the backoff it would otherwise be waiting out.
        await wait(poller, for: { $0 == .live }, timeout: 4, "live promptly after settings change")
    }
}
