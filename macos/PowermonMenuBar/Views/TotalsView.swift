import SwiftUI

/// The cost block. Amber is money and nothing else: with no tariff it shows
/// energy only, in neutral ink, never an amber zero.
struct TotalsView: View {
    let totals: Snapshot.Totals
    let meta: Snapshot.Meta

    private var money: Bool { meta.hasTariff }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(money ? "Cost" : "Energy").monoLabel()
            HStack(alignment: .top, spacing: 8) {
                column("Today", kwh: totals.today.kwh, cost: totals.today.cost)
                column("Month", kwh: totals.month.kwh, cost: totals.month.cost)
                column("Projected", kwh: totals.month.projected30DKwh, cost: totals.month.projected30DCost)
            }
            callout
        }
    }

    private func column(_ label: String, kwh: Double?, cost: Double?) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).monoLabel()
            if money {
                Text(cost.map { Format.cash($0, symbol: meta.symbol) } ?? Format.dash)
                    .font(.mono(16, .bold))
                    .foregroundStyle(Palette.money)
                Text(kwh.map { "\(Format.kwhValue($0)) kWh" } ?? Format.dash)
                    .font(.mono(10))
                    .foregroundStyle(Palette.ink2)
            } else {
                Text(kwh.map(Format.kwhValue) ?? Format.dash)
                    .font(.mono(16, .bold))
                    .foregroundStyle(Palette.ink)
                Text("kWh")
                    .font(.mono(10))
                    .foregroundStyle(Palette.ink2)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }

    /// Idle cost (or, without a tariff, idle energy) and its share of the month.
    @ViewBuilder
    private var callout: some View {
        let month = totals.month
        let share = month.idleKwh.flatMap { idle in
            month.kwh > 0 ? Int((100 * idle / month.kwh).rounded()) : nil
        }
        let text = "idle this month" + (share.map { " · \($0) % of the energy" } ?? "")
        if money, let idle = month.idleCost {
            calloutBox(Format.cash(idle, symbol: meta.symbol), text, amber: true)
        } else if !money, let idle = month.idleKwh {
            calloutBox("\(Format.kwhValue(idle)) kWh", text, amber: false)
        }
    }

    private func calloutBox(_ figure: String, _ text: String, amber: Bool) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(figure)
                .font(.mono(amber ? 22 : 18, .heavy))
                .foregroundStyle(amber ? Palette.moneyBig : Palette.ink)
                .fixedSize()
            Text(text)
                .font(.system(size: 11))
                .foregroundStyle(Palette.ink2)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(amber ? Palette.moneyWash : Palette.panel2, in: RoundedRectangle(cornerRadius: 3))
        .overlay(RoundedRectangle(cornerRadius: 3).stroke(amber ? Palette.moneyRule : Palette.rule, lineWidth: 1))
        .accessibilityElement(children: .combine)
    }
}
