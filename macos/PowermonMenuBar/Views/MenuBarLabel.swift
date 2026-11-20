import SwiftUI

/// The contested real estate: one value, never a dashboard.
struct MenuBarLabel: View {
    @ObservedObject var poller: Poller
    @ObservedObject var settings: AppSettings

    var body: some View {
        HStack(spacing: 4) {
            Image(nsImage: glyph)
            Text(title)
                // Mandatory: without monospaced digits the item changes width on
                // every update and shoves neighbouring menu bar items around.
                .font(.system(size: 13).monospacedDigit())
        }
    }

    /// The mascot. Idle is the menu bar's own text colour; a colour means
    /// something is happening: blue training, yellow alert, red unreachable.
    private var glyph: NSImage {
        switch poller.state {
        case .live:
            if poller.snapshot?.isAlerting == true { return tinted(Palette.warning) }
            return poller.snapshot?.busy == true ? tinted(Palette.gpu) : WatcherGlyph.image(color: nil)
        case .stale, .connecting:
            return WatcherGlyph.image(color: nil, opacity: 0.5)
        case .samplerStalled:
            return tinted(Palette.warning)
        case .unreachable, .unauthorized:
            return tinted(Palette.critical)
        }
    }

    private func tinted(_ color: Color) -> NSImage {
        WatcherGlyph.image(color: NSColor(color))
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
