import Foundation

/// Zero and unknown are different states. Every optional formatter here renders
/// nil as an em dash, never as 0 — a 0 °C reading is a lie when the truth is
/// "there is no sensor".
enum Format {
    static let dash = "—"

    static func watts(_ w: Double?) -> String {
        guard let w else { return dash }
        return "\(Int(w.rounded())) W"
    }

    static func costPerHour(_ cost: Double?, symbol: String) -> String {
        guard let cost else { return dash }
        return String(format: "%@%.3f/h", symbol, cost)
    }

    static func cost(_ cost: Double?, symbol: String) -> String {
        guard let cost else { return dash }
        return String(format: "%@%.2f", symbol, cost)
    }

    static func kwh(_ kwh: Double?) -> String {
        guard let kwh else { return dash }
        return String(format: "%.2f kWh", kwh)
    }

    static func temp(_ celsius: Double?) -> String {
        guard let celsius else { return dash }
        return "\(Int(celsius.rounded())) °C"
    }

    static func gib(used: Double?, total: Double?) -> String {
        guard let used, let total else { return dash }
        return String(format: "%.1f/%.0f GiB", used, total)
    }

    static func mib(_ mib: Double?) -> String {
        guard let mib else { return dash }
        return String(format: "%.1f GiB", mib / 1024)
    }

    /// "3d 20h", "4h 12m", "9m".
    static func uptime(_ seconds: Double) -> String {
        let total = Int(seconds)
        let days = total / 86_400
        let hours = (total % 86_400) / 3_600
        let minutes = (total % 3_600) / 60
        if days > 0 { return "\(days)d \(hours)h" }
        if hours > 0 { return "\(hours)h \(minutes)m" }
        return "\(minutes)m"
    }

    static func elapsed(since date: Date) -> String {
        let seconds = Int(Date().timeIntervalSince(date))
        if seconds < 60 { return "\(seconds) s ago" }
        return "\(seconds / 60) min ago"
    }

    /// Guards the divide so a fresh install (total 0) does not produce NaN.
    static func fraction(_ value: Double?, of total: Double?) -> Double? {
        guard let value, let total, total > 0 else { return nil }
        return min(max(value / total, 0), 1)
    }
}
