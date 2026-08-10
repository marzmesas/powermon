import XCTest
@testable import Powermon

final class SnapshotDecodingTests: XCTestCase {

    private func decode(_ fixture: String) throws -> Snapshot {
        let url = try XCTUnwrap(
            Bundle(for: Self.self).url(forResource: fixture, withExtension: "json"),
            "fixture \(fixture).json missing from the test bundle"
        )
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(Snapshot.self, from: Data(contentsOf: url))
    }

    /// The whole point of M1: a real payload decodes, and the snake_case keys
    /// that `.convertFromSnakeCase` could plausibly mangle actually land.
    func testDecodesLivePayload() throws {
        let s = try decode("sample-now")

        XCTAssertEqual(s.power.totalW, 104.42, accuracy: 0.01)
        XCTAssertEqual(s.meta.host, "marzmesas-trainbox")
        XCTAssertFalse(s.busy)
        XCTAssertEqual(s.spark.total.count, 90)
    }

    /// The design doc calls these out by name: verify rather than assume.
    func testAwkwardKeysDecode() throws {
        let s = try decode("sample-now")

        // cost_per_h -> costPerH
        XCTAssertEqual(s.power.costPerH, 0.0157, accuracy: 0.0001)
        // clock_mhz -> clockMhz (digits after the underscore)
        XCTAssertEqual(try XCTUnwrap(s.gpu.clockMhz), 210)
        // projected_30d_kwh -> projected30DKwh, with a CAPITAL D: the strategy
        // capitalises each component and "30d".capitalized == "30D".
        XCTAssertEqual(try XCTUnwrap(s.totals.month.projected30DKwh), 76.51, accuracy: 0.01)
        XCTAssertEqual(try XCTUnwrap(s.totals.month.projected30DCost), 11.48, accuracy: 0.01)
        // mem_mib -> memMib, inside a nested array element
        XCTAssertEqual(try XCTUnwrap(s.gpu.procs.first?.memMib), 260)
        // max_w / limit_w -> the meter denominators
        XCTAssertEqual(s.cpu.maxW, 142)
        XCTAssertEqual(try XCTUnwrap(s.gpu.limitW), 350)
        // uptime_s, total_gib
        XCTAssertEqual(s.meta.uptimeS, 339245.09, accuracy: 0.01)
        XCTAssertEqual(s.mem.totalGib, 31.27, accuracy: 0.01)
    }

    func testProcessesDecodeWithStringPIDs() throws {
        let s = try decode("sample-now")
        XCTAssertEqual(s.gpu.procs.count, 2)
        let vllm = try XCTUnwrap(s.gpu.procs.first { $0.name == "VLLM::EngineCore" })
        XCTAssertEqual(vllm.pid, "3478")   // a String in the payload, not an Int
        XCTAssertEqual(vllm.id, vllm.pid)
    }

    func testMeasuredVersusEstimated() throws {
        XCTAssertFalse(try decode("sample-now").power.isMeasured)
        XCTAssertTrue(try decode("rapl-measured").power.isMeasured)
    }

    // MARK: - The variants that decide whether the UI shows "—" or a lie

    func testGPUAbsentDecodesWithEverythingNull() throws {
        let s = try decode("gpu-absent")
        XCTAssertFalse(s.gpu.present)
        XCTAssertNil(s.gpu.limitW)
        XCTAssertNil(s.gpu.memTotal)
        XCTAssertNil(s.gpu.temp)
        XCTAssertTrue(s.gpu.procs.isEmpty)
    }

    func testGPUErrorKeepsPresentButNullsTheReadings() throws {
        let s = try decode("gpu-error")
        XCTAssertTrue(s.gpu.present)
        XCTAssertTrue(s.gpu.error)
        // nil, not 0 — a 0 W GPU is a lie when the truth is "the read failed"
        XCTAssertNil(s.gpu.temp)
        XCTAssertNil(s.gpu.memUsed)
    }

    func testNullTemperaturesDecodeAsNil() throws {
        let s = try decode("no-sensors")
        XCTAssertNil(s.cpu.temp)
        XCTAssertNil(s.gpu.temp)
    }

    func testEmptyProcessList() throws {
        XCTAssertTrue(try decode("no-procs").gpu.procs.isEmpty)
    }

    func testFreshInstallZeroTotals() throws {
        let s = try decode("fresh-install")
        XCTAssertEqual(s.totals.month.kwh, 0)
        XCTAssertEqual(s.totals.month.cost, 0)
    }

    /// The payload carries disk, net, session and totals.all, which the model
    /// deliberately omits. Decoding must ignore them, not choke on them.
    func testUnmodelledFieldsAreIgnored() throws {
        XCTAssertNoThrow(try decode("sample-now"))
    }
}
