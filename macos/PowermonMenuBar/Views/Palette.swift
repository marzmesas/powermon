import AppKit
import SwiftUI

/// The web dashboard's palette, so the two read as one product. Each colour
/// resolves itself per appearance, so no view ever branches on `colorScheme`.
enum Palette {
    static let gpu      = dynamic(light: 0x2A78D6, dark: 0x3987E5)
    static let cpu      = dynamic(light: 0xEB6834, dark: 0xD95926)
    static let other    = dynamic(light: 0x1BAF7A, dark: 0x199E70)
    static let track    = dynamic(light: 0xCDE2FB, dark: 0x184F95)
    static let primary  = dynamic(light: 0x0B0B0B, dark: 0xFFFFFF)
    static let secondary = dynamic(light: 0x52514E, dark: 0xC3C2B7)
    static let muted    = dynamic(light: 0x898781, dark: 0x898781)
    static let good     = dynamic(light: 0x0CA30C, dark: 0x0CA30C)
    static let warning  = dynamic(light: 0xFAB219, dark: 0xFAB219)
    static let critical = dynamic(light: 0xD03B3B, dark: 0xD03B3B)

    /// Meter fill: accent until 75 %, warning to 90 %, critical above.
    static func meterFill(fraction: Double, accent: Color) -> Color {
        switch fraction {
        case ..<0.75: return accent
        case ..<0.90: return warning
        default:      return critical
        }
    }

    private static func dynamic(light: UInt32, dark: UInt32) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            let isDark = appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
            return NSColor(hex: isDark ? dark : light)
        })
    }
}

private extension NSColor {
    convenience init(hex: UInt32) {
        self.init(
            srgbRed: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255,
            alpha: 1
        )
    }
}
