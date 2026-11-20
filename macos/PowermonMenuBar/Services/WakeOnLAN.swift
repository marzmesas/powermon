import Foundation

/// Wake-on-LAN: a UDP broadcast of the "magic packet". Only reaches machines on
/// the same local network; a broadcast does not cross a VPN.
enum WakeOnLAN {
    /// 6 x 0xFF then the MAC 16 times (102 bytes). Accepts ":" or "-"
    /// separators in either case; nil for anything else.
    static func magicPacket(mac: String) -> Data? {
        let parts = mac.split(omittingEmptySubsequences: false) { $0 == ":" || $0 == "-" }
        let bytes = parts.compactMap { $0.count == 2 && $0.allSatisfy(\.isHexDigit) ? UInt8($0, radix: 16) : nil }
        guard parts.count == 6, bytes.count == 6 else { return nil }
        return Data(repeating: 0xFF, count: 6) + Data((0..<16).flatMap { _ in bytes })
    }

    /// Broadcasts to 255.255.255.255:9, three times, 300 ms apart. A plain BSD
    /// socket, because NWConnection often refuses the broadcast address.
    @discardableResult
    static func send(mac: String) async -> Bool {
        guard let packet = magicPacket(mac: mac) else { return false }
        let fd = socket(AF_INET, SOCK_DGRAM, 0)
        guard fd >= 0 else { return false }
        defer { close(fd) }
        var on: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_BROADCAST, &on, socklen_t(MemoryLayout<Int32>.size))

        var addr = sockaddr_in()
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = UInt16(9).bigEndian
        addr.sin_addr.s_addr = INADDR_BROADCAST.bigEndian

        var ok = false
        for i in 0..<3 {
            if i > 0 { try? await Task.sleep(nanoseconds: 300_000_000) }
            let n = packet.withUnsafeBytes { buf in
                withUnsafePointer(to: &addr) {
                    $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                        sendto(fd, buf.baseAddress, buf.count, 0, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
                    }
                }
            }
            ok = ok || n == packet.count
        }
        return ok
    }
}
