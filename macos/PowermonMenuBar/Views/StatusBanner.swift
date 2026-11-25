import SwiftUI

/// Stale / unreachable / 401 / wedged messaging. Each state names the actual
/// problem, because each has a different fix.
struct StatusBanner: View {
    @State private var woke = false

    let state: ConnectionState
    let host: String?
    /// Remembered MAC of the server, if any: enables Wake while unreachable.
    var mac: String?
    let onRetry: () -> Void
    let onSettings: () -> Void

    var body: some View {
        if let message {
            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Circle()
                        .fill(tint)
                        .frame(width: 6, height: 6)
                    Text(message)
                        .font(.system(size: 11))
                        .foregroundStyle(Palette.ink2)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 8)
                    if showsActions {
                        HStack(spacing: 8) {
                            if case .unauthorized = state {} else {
                                Button("Retry", action: onRetry)
                            }
                            Button("Settings", action: onSettings)
                            if case .unreachable = state, let mac {
                                Button("Wake") {
                                    woke = true
                                    Task.detached { await WakeOnLAN.send(mac: mac) }
                                }
                            }
                        }
                        .buttonStyle(TextButtonStyle())
                    }
                }
                if case .unreachable = state, mac != nil {
                    Text(woke ? "Wake signal sent. It can take a minute. Works only on the same local network as the server, not over a VPN."
                              : "Wake works only on the same local network as the server, not over a VPN.")
                        .font(.system(size: 10))
                        .foregroundStyle(Palette.ink3)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(8)
            .background(tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 6))
        }
    }

    private var message: String? {
        switch state {
        case .live, .connecting:
            return nil
        case .stale(let since):
            return "Last updated \(Format.elapsed(since: since))"
        case .unreachable(let reason):
            return "Can't reach \(host ?? "the server") — \(reason)"
        case .unauthorized:
            return "Token rejected. Check the token in Settings."
        case .samplerStalled:
            return "Server is up but not sampling."
        }
    }

    private var showsActions: Bool {
        switch state {
        case .unreachable, .unauthorized: return true
        default: return false
        }
    }

    private var tint: Color {
        switch state {
        case .unreachable, .unauthorized: return Palette.crit
        case .samplerStalled, .stale:     return Palette.warn
        case .live, .connecting:          return Palette.ink3
        }
    }
}
