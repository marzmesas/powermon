import XCTest
@testable import Powermon

/// The spoken sentence must match the dashboard's `speak()` word for word, and
/// the gauge scale must stay above everything the snapshot says it has seen.
final class SpeechTests: XCTestCase {

    private func snapshot(_ edit: (inout [String: Any]) -> Void = { _ in }) throws -> Snapshot {
        let url = try XCTUnwrap(Bundle(for: Self.self).url(forResource: "sample-now", withExtension: "json"))
        var json = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any])
        json["power"] = ["total_w": 475.0, "cpu_w": 74.0, "gpu_w": 319.0, "other_w": 82.0,
                         "cost_per_h": 0.1425, "source": "rapl"]
        edit(&json)
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(Snapshot.self, from: JSONSerialization.data(withJSONObject: json))
    }

    private func say(_ s: Snapshot, _ state: ConnectionState = .live) -> String {
        Speech.plain(Speech.pieces(snapshot: s, state: state))
    }

    func testWorkingWithARun() throws {
        let s = try snapshot { json in
            json["busy"] = true
            json["run"] = ["id": 1, "start": Date().timeIntervalSince1970 - 600, "seconds": 600.0,
                           "kwh": 0.1, "cost": 0.8, "name": NSNull(), "running": true]
        }
        let pieces = Speech.pieces(snapshot: s, state: .live)
        XCTAssertTrue(say(s).hasPrefix("Working: 475 W, which is €0.14 an hour. This run has cost €0.80 since "))
        XCTAssertEqual(pieces.filter { $0.style == .money }.map(\.text), ["€0.14 an hour", "€0.80"])
        XCTAssertEqual(pieces.filter { $0.style == .watts }.map(\.text), ["475 W"])
    }

    func testIdle() throws {
        XCTAssertEqual(try say(snapshot()), "Idle at 475 W. That is €3.42 a day for doing nothing.")
    }

    func testIdleAlertNamesTheMinutesOnlyWhenKnown() throws {
        let s = try snapshot { json in
            json["alerts"] = [["kind": "idle", "message": "m", "since": 1_000.0]]
            json["ts"] = 1_000.0 + 720
        }
        XCTAssertEqual(say(s), "Idle, but drawing 475 W for 12 minutes, above the idle alert threshold. That is €3.42 a day.")
        let unknown = try snapshot { $0["alert"] = ["active": true] }
        XCTAssertEqual(say(unknown), "Idle, but drawing 475 W, above the idle alert threshold. That is €3.42 a day.")
    }

    func testOtherAlertsAreAppended() throws {
        let s = try snapshot { json in
            json["alerts"] = [["kind": "gpu_hot", "message": "GPU at 88 °C"], ["kind": "disk", "message": "x"]]
        }
        XCTAssertEqual(say(s), "Idle at 475 W. That is €3.42 a day for doing nothing. Alert: GPU at 88 °C, and 1 more.")
    }

    func testNoTariffSaysEnergyNotMoney() throws {
        let s = try snapshot { json in
            json["busy"] = true
            var meta = json["meta"] as! [String: Any]
            meta["mode"] = "none"
            json["meta"] = meta
        }
        XCTAssertEqual(say(s), "Working: 475 W.")
        XCTAssertTrue(Speech.pieces(snapshot: s, state: .live).allSatisfy { $0.style != .money })
        let idle = try snapshot { json in
            var meta = json["meta"] as! [String: Any]
            meta["mode"] = "none"
            json["meta"] = meta
        }
        XCTAssertEqual(say(idle), "Idle at 475 W. That is 11.40 kWh a day for doing nothing.")
    }

    func testStaleAndUnreachableNeverClaimACause() throws {
        let s = try snapshot()
        XCTAssertEqual(say(s, .stale(since: Date().addingTimeInterval(-30))),
                       "No reading for 30 seconds. The figures are the last ones received.")
        XCTAssertEqual(say(s, .stale(since: Date().addingTimeInterval(-300))),
                       "No reading for 5 minutes. The figures are the last ones received.")
        XCTAssertTrue(say(s, .unreachable("timed out")).hasPrefix("Powermon is not answering."))
        XCTAssertTrue(say(s, .unauthorized).hasPrefix("Not authorised"))
    }

    func testStateWordsFollowTheDashboard() throws {
        XCTAssertEqual(PopoverState(snapshot: try snapshot(), connection: .live), .idle)
        XCTAssertEqual(PopoverState(snapshot: try snapshot { $0["busy"] = true }, connection: .live), .working)
        XCTAssertEqual(PopoverState(snapshot: try snapshot { $0["alert"] = ["active": true] }, connection: .live), .alert)
        XCTAssertEqual(PopoverState(snapshot: try snapshot(), connection: .unreachable("x")), .noData)
        XCTAssertEqual(PopoverState(snapshot: try snapshot(), connection: .unauthorized).word, "Not authorised")
    }

    func testGaugeScaleIsRoundAndAboveTheValue() {
        XCTAssertEqual(Format.niceMax(above: 0), 100)
        XCTAssertEqual(Format.niceMax(above: 104), 150)
        XCTAssertEqual(Format.niceMax(above: 458.7), 500)
        XCTAssertEqual(Format.niceMax(above: 500), 600)
        XCTAssertEqual(Format.niceMax(above: 1_234), 1_400)
        for w in stride(from: 0.0, through: 2_000, by: 37) { XCTAssertGreaterThan(Format.niceMax(above: w), w) }
    }

    func testNewOptionalTotalsDecode() throws {
        let s = try snapshot()
        XCTAssertEqual(try XCTUnwrap(s.totals.month.peakW), 458.7, accuracy: 0.1)
        XCTAssertEqual(try XCTUnwrap(s.totals.all?.peakW), 458.7, accuracy: 0.1)
        XCTAssertEqual(try XCTUnwrap(s.totals.month.idleKwh), 19.57, accuracy: 0.01)
        XCTAssertTrue(s.meta.hasTariff)
    }
}
