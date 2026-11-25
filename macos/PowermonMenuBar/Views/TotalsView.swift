import SwiftUI

/// The cost row. Amber is money and nothing else: with no tariff it shows
/// energy only, in neutral ink, never an amber zero.
struct TotalsView: View {
    let totals: Snapshot.Totals
    let meta: Snapshot.Meta

    private var money: Bool { meta.hasTariff }

    var body: some View {
        HStack(alignment: .center, spacing: 8) {
            cell("Today", kwh: totals.today.kwh, cost: totals.today.cost)
            cell("Month", kwh: totals.month.kwh, cost: totals.month.cost)
            cell("Projected", kwh: totals.month.projected30DKwh, cost: totals.month.projected30DCost)
            callout
        }
    }

    /// The collapsed card's one line: money in amber, or energy in neutral ink.
    var summary: Text {
        let month = totals.month
        func part(_ label: String, cost: Double?, kwh: Double?) -> Text? {
            if money {
                guard let cost else { return nil }
                return Text("\(label) \(Format.cash(cost, symbol: meta.symbol))").foregroundColor(Palette.money)
            }
            guard let kwh else { return nil }
            return Text("\(label) \(Format.kwhValue(kwh)) kWh").foregroundColor(Palette.ink)
        }
        let parts = [part("today", cost: totals.today.cost, kwh: totals.today.kwh),
                     part("month", cost: month.cost, kwh: month.kwh),
                     part("idle", cost: month.idleCost, kwh: month.idleKwh)].compactMap { $0 }
        let dot = Text(" · ").foregroundColor(Palette.ink3)
        return parts.dropFirst().reduce(parts.first ?? Text(Format.dash)) { $0 + dot + $1 }
    }

    private func cell(_ label: String, kwh: Double?, cost: Double?) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).monoLabel()
            if money {
                Text(cost.map { Format.cash($0, symbol: meta.symbol) } ?? Format.dash)
                    .font(.mono(17, .bold))
                    .foregroundStyle(Palette.money)
            } else {
                HStack(alignment: .firstTextBaseline, spacing: 3) {
                    Text(kwh.map(Format.kwhValue) ?? Format.dash)
                        .font(.mono(17, .bold))
                        .foregroundStyle(Palette.ink)
                    Text("kWh").font(.mono(10)).foregroundStyle(Palette.ink3)
                }
            }
        }
        .lineLimit(1)
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(money ? "\(label) cost" : "\(label) energy")
    }

    /// Idle cost (or, without a tariff, idle energy) and its share of the month.
    @ViewBuilder
    private var callout: some View {
        let month = totals.month
        let share = month.idleKwh.flatMap { idle in
            month.kwh > 0 ? Int((100 * idle / month.kwh).rounded()) : nil
        }
        let sub = share.map { "\($0) % of energy" }
        if money, let idle = month.idleCost {
            calloutBox(Format.cash(idle, symbol: meta.symbol), sub, amber: true)
        } else if !money, let idle = month.idleKwh {
            calloutBox("\(Format.kwhValue(idle)) kWh", sub, amber: false)
        } else {
            Color.clear.frame(width: 0, height: 0)
        }
    }

    private func calloutBox(_ figure: String, _ sub: String?, amber: Bool) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("Idle this month").monoLabel()
            Text(figure)
                .font(.mono(17, .heavy))
                .foregroundStyle(amber ? Palette.moneyBig : Palette.ink)
            if let sub {
                Text(sub).font(.mono(9.5)).foregroundStyle(Palette.ink2)
            }
        }
        .lineLimit(1)
        .fixedSize()
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(amber ? Palette.moneyWash : Palette.panel2, in: RoundedRectangle(cornerRadius: 3))
        .overlay(RoundedRectangle(cornerRadius: 3).stroke(amber ? Palette.moneyRule : Palette.rule, lineWidth: 1))
        .accessibilityElement(children: .combine)
    }
}
