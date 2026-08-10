import SwiftUI

/// One labelled meter: name, value, bar, optional trailing note.
/// A nil `fraction` means unknown — the track renders empty rather than full-zero.
struct MeterRow: View {
    let label: String
    let value: String
    let fraction: Double?
    let accent: Color
    var trailing: String?

    var body: some View {
        HStack(spacing: 8) {
            Text(label)
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(Palette.secondary)
                .frame(width: 38, alignment: .leading)

            Text(value)
                .font(.system(size: 11).monospacedDigit())
                .foregroundStyle(Palette.primary)
                .frame(width: 82, alignment: .leading)

            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule()
                        .fill(Palette.track)
                    if let fraction {
                        Capsule()
                            .fill(Palette.meterFill(fraction: fraction, accent: accent))
                            .frame(width: max(2, geo.size.width * fraction))
                    }
                }
            }
            .frame(height: 6)

            if let trailing {
                Text(trailing)
                    .font(.system(size: 11).monospacedDigit())
                    .foregroundStyle(Palette.secondary)
                    .frame(width: 42, alignment: .trailing)
            }
        }
    }
}
