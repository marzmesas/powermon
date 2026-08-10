import Foundation

enum ConnectionState: Equatable {
    case connecting
    case live
    /// No successful poll in 3× the refresh interval. Measured from local
    /// elapsed time since the last success, never by comparing to the server's
    /// `ts` — the two clocks drift.
    case stale(since: Date)
    case unreachable(String)
    case unauthorized
    /// Reachable and sampling stopped: `healthz.ok == false`.
    case samplerStalled

    /// Whether the menu bar may still show the last known number. Unreachable
    /// and 401 must not — a stale reading looks live.
    var showsLastValue: Bool {
        switch self {
        case .live, .stale, .samplerStalled: return true
        case .connecting, .unreachable, .unauthorized: return false
        }
    }
}
