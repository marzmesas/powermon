import SwiftUI

/// Temperatures as plain numbers, memory as a number over a thin capacity bar
/// that turns warn at 75 % and crit at 90 %. Laid out two to a row.
struct InstrumentStrip: View {
    struct Item: Identifiable {
        let label: String
        let value: String
        let unit: String
        var fraction: Double?
        var accent: Color = Palette.rest
        /// Capacities carry a bar slot; temperatures leave it empty.
        var hasBar = false
        var id: String { label }
    }

    let items: [Item]

    var body: some View {
        let rows = stride(from: 0, to: items.count, by: 2).map { Array(items[$0..<min($0 + 2, items.count)]) }
        VStack(spacing: 8) {
            ForEach(rows.indices, id: \.self) { r in
                HStack(alignment: .top, spacing: 12) {
                    ForEach(rows[r]) { cell($0) }
                    if rows[r].count == 1 { Color.clear.frame(maxWidth: .infinity) }
                }
            }
        }
    }

    private func cell(_ item: Item) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(item.label).monoLabel()
            HStack(alignment: .firstTextBaseline, spacing: 3) {
                Text(item.value)
                    .font(.mono(15, .bold))
                    .foregroundStyle(Palette.ink)
                Text(item.unit)
                    .font(.mono(10))
                    .foregroundStyle(Palette.ink3)
            }
            if item.hasBar {
                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        Capsule().fill(Palette.track)
                        if let f = item.fraction {
                            Capsule()
                                .fill(Palette.capacityFill(fraction: f, accent: item.accent))
                                .frame(width: max(2, geo.size.width * f))
                        }
                    }
                }
                .frame(height: 3)
                .accessibilityHidden(true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(item.label)
        .accessibilityValue("\(item.value) \(item.unit)"
            + (item.fraction.map { ", \(Int(($0 * 100).rounded())) percent full" } ?? ""))
    }
}
