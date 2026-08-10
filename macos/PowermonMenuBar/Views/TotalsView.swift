import SwiftUI

struct TotalsView: View {
    let totals: Snapshot.Totals
    let symbol: String

    var body: some View {
        VStack(spacing: 4) {
            row("Today", kwh: totals.today.kwh, cost: totals.today.cost)
            row("Month", kwh: totals.month.kwh, cost: totals.month.cost)
            row("Projected",
                kwh: totals.month.projected30DKwh,
                cost: totals.month.projected30DCost)
        }
    }

    private func row(_ label: String, kwh: Double?, cost: Double?) -> some View {
        HStack {
            Text(label)
                .font(.system(size: 11))
                .foregroundStyle(Palette.secondary)
            Spacer()
            Text(Format.kwh(kwh))
                .font(.system(size: 11).monospacedDigit())
                .foregroundStyle(Palette.primary)
                .frame(width: 76, alignment: .trailing)
            Text(Format.cost(cost, symbol: symbol))
                .font(.system(size: 11).monospacedDigit())
                .foregroundStyle(Palette.primary)
                .frame(width: 56, alignment: .trailing)
        }
    }
}
