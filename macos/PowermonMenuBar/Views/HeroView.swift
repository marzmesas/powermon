import SwiftUI

/// Exactly one hero: watts now, cost per hour, and the shape of the last 3 min.
struct HeroView: View {
    let snapshot: Snapshot

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text(Format.watts(snapshot.power.totalW))
                    .font(.system(size: 34, weight: .semibold))
                    .foregroundStyle(Palette.primary)
                Spacer()
                Text(Format.costPerHour(snapshot.power.costPerH, symbol: snapshot.meta.symbol))
                    .font(.system(size: 17, weight: .medium))
                    .foregroundStyle(Palette.secondary)
            }
            Sparkline(values: snapshot.spark.total)
                .frame(height: 48)
        }
    }
}
