import XCTest
@testable import Powermon

final class AlertEdgeDetectorTests: XCTestCase {
    func testActiveOnFirstSnapshotNotifiesOnce() {
        var detector = AlertEdgeDetector()
        XCTAssertEqual(detector.observe(active: true), .raised)
        XCTAssertNil(detector.observe(active: true))
    }

    func testUnchangedStateIsSilent() {
        var detector = AlertEdgeDetector()
        XCTAssertNil(detector.observe(active: false))
        XCTAssertNil(detector.observe(active: false))
    }

    func testClearingGivesAllClearOnce() {
        var detector = AlertEdgeDetector()
        XCTAssertEqual(detector.observe(active: true), .raised)
        XCTAssertEqual(detector.observe(active: false), .cleared)
        XCTAssertNil(detector.observe(active: false))
        XCTAssertEqual(detector.observe(active: true), .raised)
    }

    func testAbsentAlertIsInactive() {
        // Poller passes `snapshot.alert?.active ?? false`.
        var detector = AlertEdgeDetector()
        let absent: Snapshot.Alert? = nil
        XCTAssertNil(detector.observe(active: absent?.active ?? false))
        XCTAssertEqual(detector.observe(active: true), .raised)
        XCTAssertEqual(detector.observe(active: absent?.active ?? false), .cleared)
    }
}
