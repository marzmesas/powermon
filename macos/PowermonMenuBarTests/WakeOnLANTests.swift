import XCTest
@testable import Powermon

final class WakeOnLANTests: XCTestCase {

    func testPacketLayout() throws {
        let p = try XCTUnwrap(WakeOnLAN.magicPacket(mac: "aa:bb:cc:dd:ee:01"))
        XCTAssertEqual(p.count, 102)
        XCTAssertEqual(Array(p.prefix(6)), Array(repeating: 0xFF, count: 6))
        for i in 0..<16 {
            XCTAssertEqual(Array(p[(6 + i * 6)..<(12 + i * 6)]), [0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0x01])
        }
    }

    func testDashesAndUppercaseAccepted() {
        XCTAssertEqual(WakeOnLAN.magicPacket(mac: "AA-BB-CC-DD-EE-01"),
                       WakeOnLAN.magicPacket(mac: "aa:bb:cc:dd:ee:01"))
        XCTAssertNotNil(WakeOnLAN.magicPacket(mac: "AA-BB-CC-DD-EE-01"))
    }

    func testMalformedReturnsNil() {
        for bad in ["", "aa:bb:cc:dd:ee", "aa:bb:cc:dd:ee:01:02", "aa:bb:cc:dd:ee:gg",
                    "aabbccddee01", "a:b:c:d:e:f", "aa:bb:cc:dd:ee:+1", "aa:bb-cc:dd:ee:01x"] {
            XCTAssertNil(WakeOnLAN.magicPacket(mac: bad), bad)
        }
    }
}
