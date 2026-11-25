import SwiftUI

/// One row of small readouts. Temperatures are plain numbers; a memory value
/// turns warn at 75 % full and crit at 90 %, and says so in its accessibility value.
struct InstrumentStrip: View {
    struct Item: Identifiable {
        let label: String
        let value: String
        let unit: String
        /// Set for memories only.
        var fraction: Double?
        var id: String { label }
    }

    let items: [Item]

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            ForEach(items) { item in
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.label).monoLabel().lineLimit(1)
                    HStack(alignment: .firstTextBaseline, spacing: 1) {
                        Text(item.value)
                            .font(.mono(12, .bold))
                            .foregroundStyle(item.fraction.map { Palette.capacityFill(fraction: $0, accent: Palette.ink) } ?? Palette.ink)
                        Text(item.unit)
                            .font(.mono(9.5))
                            .foregroundStyle(Palette.ink3)
                    }
                    .lineLimit(1)
                    .fixedSize()
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(item.label)
                .accessibilityValue("\(item.value) \(item.unit)"
                    + (item.fraction.map { ", \(Int(($0 * 100).rounded())) percent full" } ?? ""))
            }
        }
    }
}
