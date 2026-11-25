import SwiftUI

/// GPU / CPU / REST as segmented LED bars on one shared watt scale. A nil
/// reading lights nothing and prints a dash, never a zero.
struct WhereItGoesView: View {
    struct Row: Identifiable {
        let label: String
        let watts: Double?
        let color: Color
        var id: String { label }
    }

    let rows: [Row]
    /// The gauge's maximum.
    let scaleMax: Double

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            ForEach(rows) { row in
                HStack(spacing: 8) {
                    Text(row.label).monoLabel().frame(width: 28, alignment: .leading)
                    LedBar(fraction: row.watts.map { $0 / scaleMax } ?? 0, color: row.color)
                        .frame(height: 9)
                    Text(Format.watts(row.watts))
                        .font(.mono(11, .semibold))
                        .foregroundStyle(Palette.ink)
                        .frame(width: 50, alignment: .trailing)
                }
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(row.label)
                .accessibilityValue(row.watts.map { "\(Format.wattsValue($0)) watts" } ?? "no reading")
            }
        }
    }
}

/// Small rounded segments, lit in the meaning colour, unlit in `track`.
struct LedBar: View {
    let fraction: Double
    let color: Color

    var body: some View {
        Canvas { context, size in
            let segment: CGFloat = 3, gap: CGFloat = 1.5
            let count = max(1, Int((size.width + gap) / (segment + gap)))
            let lit = Int((Double(count) * min(max(fraction, 0), 1)).rounded(.up))
            for i in 0..<count {
                let rect = CGRect(x: CGFloat(i) * (segment + gap), y: 0, width: segment, height: size.height)
                context.fill(Path(roundedRect: rect, cornerRadius: 1),
                             with: .color(i < lit ? color : Palette.track))
            }
        }
        .accessibilityHidden(true)
    }
}
