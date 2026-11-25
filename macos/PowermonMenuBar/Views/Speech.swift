import Foundation

/// What the popover calls itself right now: one word on the chip, one colour
/// for the hero. Mirrors the dashboard's `applyState`.
enum PopoverState: Equatable {
    case working, idle, alert, noData, notAuthorised

    init(snapshot: Snapshot?, connection: ConnectionState) {
        switch connection {
        case .unauthorized: self = .notAuthorised
        case .stale, .unreachable, .samplerStalled: self = .noData
        case .live, .connecting:
            guard let snapshot else { self = .noData; return }
            self = snapshot.isAlerting ? .alert : snapshot.busy ? .working : .idle
        }
    }

    var word: String {
        switch self {
        case .working: return "Working"
        case .idle: return "Idle"
        case .alert: return "Alert"
        case .noData: return "No data"
        case .notAuthorised: return "Not authorised"
        }
    }
}

/// The one spoken sentence, ported from the dashboard's `speak()` so both
/// clients say the same thing. Built only from what the payload says; it never
/// guesses at a cause. Pure, so the wording is testable.
enum Speech {
    enum Style: Equatable { case plain, watts, money }
    struct Piece: Equatable {
        let text: String
        let style: Style
    }

    static func pieces(
        snapshot s: Snapshot, state: ConnectionState,
        now: Date = Date(), calendar: Calendar = .current
    ) -> [Piece] {
        var out: [Piece] = []
        func put(_ text: String, _ style: Style = .plain) { out.append(Piece(text: text, style: style)) }

        switch state {
        case .unauthorized:
            return [Piece(text: "Not authorised to read this powermon, so nothing here is current.", style: .plain)]
        case .stale(let since):
            let seconds = Int(now.timeIntervalSince(since))
            let minutes = Int((Double(seconds) / 60).rounded())
            let span = seconds < 90 ? "\(seconds) seconds" : "\(minutes) minute\(minutes == 1 ? "" : "s")"
            return [Piece(text: "No reading for \(span). The figures are the last ones received.", style: .plain)]
        case .unreachable:
            return [Piece(text: "Powermon is not answering. The figures are the last ones received.", style: .plain)]
        case .samplerStalled:
            return [Piece(text: "Powermon is answering but not sampling. The figures are the last ones received.", style: .plain)]
        case .live, .connecting:
            break
        }

        let symbol = s.meta.symbol
        let watts = "\(Format.wattsValue(s.power.totalW)) W"
        let money = s.meta.hasTariff
        let hourly = s.power.costPerH
        let idleAlert = (s.alerts ?? []).first { $0.kind == "idle" }
        let others = (s.alerts ?? []).filter { $0.kind != "idle" }

        func since(_ ts: Double) -> String {
            let date = Date(timeIntervalSince1970: ts)
            let formatter = DateFormatter()
            formatter.calendar = calendar
            formatter.timeZone = calendar.timeZone
            if calendar.isDate(date, inSameDayAs: now) {
                formatter.dateFormat = "HH:mm"
            } else {
                formatter.dateFormat = "d MMM, HH:mm"
            }
            return formatter.string(from: date)
        }

        if !s.busy, s.alert?.active == true || idleAlert != nil {
            put("Idle, but drawing ")
            put(watts, .watts)
            if let since = idleAlert?.since {
                let mins = max(1, Int(((s.ts - since) / 60).rounded()))
                put(" for \(mins) minute\(mins == 1 ? "" : "s")")
            }
            put(", above the idle alert threshold.")
            if money {
                put(" That is ")
                put(Format.cash(hourly * 24, symbol: symbol), .money)
                put(" a day.")
            }
        } else if s.busy {
            put("Working: ")
            put(watts, .watts)
            if money {
                put(", which is ")
                put("\(Format.cash(hourly, symbol: symbol)) an hour", .money)
            }
            put(".")
            if let run = s.run, run.start > 0 {
                if money {
                    put(" This run has cost ")
                    put(Format.cash(run.cost, symbol: symbol), .money)
                } else {
                    put(" This run has used \(Format.kwhValue(run.kwh)) kWh")
                }
                put(" since \(since(run.start)).")
            }
        } else {
            put("Idle at ")
            put(watts, .watts)
            put(".")
            if money {
                put(" That is ")
                put("\(Format.cash(hourly * 24, symbol: symbol)) a day", .money)
                put(" for doing nothing.")
            } else {
                put(" That is \(Format.kwhValue(s.power.totalW * 24 / 1000)) kWh a day for doing nothing.")
            }
        }
        if let first = others.first {
            put(" Alert: \(first.message)" + (others.count > 1 ? ", and \(others.count - 1) more." : "."))
        }
        return out
    }

    static func plain(_ pieces: [Piece]) -> String { pieces.map(\.text).joined() }
}
