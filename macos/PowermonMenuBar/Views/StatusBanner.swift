import SwiftUI

/// Stale / unreachable / 401 / wedged messaging. Each state names the actual
/// problem, because each has a different fix.
struct StatusBanner: View {
    let state: ConnectionState
    let host: String?
    let onRetry: () -> Void
    let onSettings: () -> Void

    var body: some View {
        if let message {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 6) {
                    Circle()
                        .fill(tint)
                        .frame(width: 6, height: 6)
                    Text(message)
                        .font(.system(size: 11))
                        .foregroundStyle(Palette.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if showsActions {
                    HStack(spacing: 8) {
                        if case .unauthorized = state {} else {
                            Button("Retry", action: onRetry)
                        }
                        Button("Settings", action: onSettings)
                    }
                    .buttonStyle(.link)
                    .font(.system(size: 11))
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(8)
            .background(tint.opacity(0.10), in: RoundedRectangle(cornerRadius: 6))
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
        case .unreachable, .unauthorized: return Palette.critical
        case .samplerStalled, .stale:     return Palette.warning
        case .live, .connecting:          return Palette.muted
        }
    }
}
