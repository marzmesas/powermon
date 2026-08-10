import SwiftUI

/// The contested real estate: one value, never a dashboard.
struct MenuBarLabel: View {
    @ObservedObject var poller: Poller
    @ObservedObject var settings: AppSettings

    var body: some View {
        HStack(spacing: 4) {
            Circle()
                .fill(dotColor)
                .frame(width: 7, height: 7)
            Text(title)
                // Mandatory: without monospaced digits the item changes width on
                // every update and shoves neighbouring menu bar items around.
                .font(.system(size: 13).monospacedDigit())
        }
    }

    private var dotColor: Color {
        switch poller.state {
        case .live:           return poller.snapshot?.busy == true ? Palette.gpu : Palette.muted
        case .stale:          return Palette.muted.opacity(0.5)
        case .samplerStalled: return Palette.warning
        case .connecting:     return Palette.muted
        case .unreachable, .unauthorized: return Palette.critical
        }
    }

    private var title: String {
        guard poller.state.showsLastValue, let snapshot = poller.snapshot else {
            return poller.state == .connecting ? "…" : Format.dash
        }
        let symbol = snapshot.meta.symbol
        switch settings.format {
        case .watts: return Format.watts(snapshot.power.totalW)
        case .cost:  return Format.costPerHour(snapshot.power.costPerH, symbol: symbol)
        case .both:
            return "\(Format.watts(snapshot.power.totalW)) · "
                + String(format: "%@%.2f/h", symbol, snapshot.power.costPerH)
        }
    }
}
