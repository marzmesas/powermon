import SwiftUI

/// Cost on one line: small mono labels, amber figures. Amber is money and
/// nothing else, so with no tariff the same line shows energy in neutral ink.
/// When the full line does not fit it drops PROJECTED, then IDLE; a figure is
/// never wrapped or truncated.
struct CostLine: View {
    let totals: Snapshot.Totals
    let meta: Snapshot.Meta

    private struct Part {
        let label: String
        let spoken: String
        let figure: String
    }

    private var money: Bool { meta.hasTariff }

    private func part(_ label: String, spoken: String, cost: Double?, kwh: Double?) -> Part? {
        if money {
            guard let cost else { return nil }
            let figure = Format.cash(cost, symbol: meta.symbol)
            return Part(label: label, spoken: "\(spoken) \(figure)", figure: figure)
        }
        guard let kwh else { return nil }
        let figure = "\(Format.kwhValue(kwh)) kWh"
        return Part(label: label, spoken: "\(spoken) \(figure)", figure: figure)
    }

    private var today: Part? { part("Today", spoken: "Today", cost: totals.today.cost, kwh: totals.today.kwh) }
    private var month: Part? { part("Month", spoken: "Month", cost: totals.month.cost, kwh: totals.month.kwh) }
    private var projected: Part? {
        part("Projected", spoken: "Projected", cost: totals.month.projected30DCost, kwh: totals.month.projected30DKwh)
    }
    private var idle: Part? {
        part("Idle", spoken: "Idle this month", cost: totals.month.idleCost, kwh: totals.month.idleKwh)
    }

    var body: some View {
        let variants = [[today, month, projected, idle], [today, month, idle], [today, month]]
            .map { $0.compactMap { $0 } }
            .filter { !$0.isEmpty }
        if let first = variants.first {
            ViewThatFits(in: .horizontal) {
                ForEach(variants.indices, id: \.self) { line(variants[$0]) }
                // Last resort, so there is always something that fits.
                line(Array(first.prefix(2)))
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(first.map(\.spoken).joined(separator: ", "))
        }
    }

    private func line(_ parts: [Part]) -> some View {
        HStack(spacing: 0) {
            ForEach(parts.indices, id: \.self) { i in
                if i > 0 { Text("  ·  ").foregroundStyle(Palette.ink3) }
                Text(parts[i].label.uppercased() + " ")
                    .font(.mono(9, .semibold))
                    .tracking(0.6)
                    .foregroundStyle(Palette.ink3)
                Text(parts[i].figure)
                    .font(.mono(11, .bold))
                    .foregroundStyle(money ? Palette.money : Palette.ink)
            }
        }
        .lineLimit(1)
        .fixedSize()
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
