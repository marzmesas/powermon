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

    /// The bare number the hero prints large, with no unit.
    static func wattsValue(_ w: Double) -> String { "\(Int(w.rounded()))" }

    /// Dashboard `fmt.kwh`: two decimals, none from 100 up.
    static func kwhValue(_ kwh: Double) -> String {
        String(format: kwh >= 100 ? "%.0f" : "%.2f", kwh)
    }

    /// Dashboard `cash`: two decimals, none from 100 up.
    static func cash(_ value: Double, symbol: String) -> String {
        symbol + String(format: abs(value) >= 100 ? "%.0f" : "%.2f", value)
    }

    /// A round maximum strictly above `watts`, for a scale people can read:
    /// steps of 50 to 200, 100 to 600, 200 beyond; never below 100.
    static func niceMax(above watts: Double) -> Double {
        let step: Double = watts < 200 ? 50 : watts < 600 ? 100 : 200
        return max(100, (watts / step).rounded(.down) * step + step)
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

    /// "Run: 2h 14m · 1.06 kWh · €0.32", plus " · name" when the process is known.
    static func run(_ run: Snapshot.Run, symbol: String) -> String {
        var parts = ["Run: \(uptime(run.seconds))", kwh(run.kwh), cost(run.cost, symbol: symbol)]
        if let name = run.name { parts.append(name) }
        return parts.joined(separator: " · ")
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
