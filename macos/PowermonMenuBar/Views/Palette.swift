import AppKit
import SwiftUI

/// The web dashboard's palette, so the two read as one product. Each colour
/// resolves itself per appearance, so no view ever branches on `colorScheme`.
enum Palette {
    static let ground    = dynamic(light: 0xEDF2EF, dark: 0x0B1310)
    static let panel     = dynamic(light: 0xFFFFFF, dark: 0x111C18)
    static let panel2    = dynamic(light: 0xF5F8F6, dark: 0x16241F)
    static let heroWash  = dynamic(light: 0xE2F2EA, dark: 0x1B4034)
    static let rule      = dynamic(light: 0xD3DDD8, dark: 0x213129)
    static let ruleStrong = dynamic(light: 0xB3C3BB, dark: 0x30463C)
    static let ink       = dynamic(light: 0x0E1B16, dark: 0xE7F2ED)
    static let ink2      = dynamic(light: 0x3C4D45, dark: 0xAAC0B6)
    static let ink3      = dynamic(light: 0x56685F, dark: 0x849A90)
    static let track     = dynamic(light: 0xDDE6E1, dark: 0x1C2C26)
    /// Mint is live power and the GPU; blue the CPU; green-grey everything else.
    static let power     = dynamic(light: 0x0C8459, dark: 0x4EEAAC)
    static let gpu       = dynamic(light: 0x10A973, dark: 0x4EEAAC)
    static let cpu       = dynamic(light: 0x1479C4, dark: 0x56B9FF)
    static let rest      = dynamic(light: 0x5E776C, dark: 0x86A497)
    /// Amber is money and nothing else. `moneyBig` is for large figures only.
    static let money     = dynamic(light: 0xA36200, dark: 0xFFBB1C)
    static let moneyBig  = dynamic(light: 0xC47A00, dark: 0xFFBB1C)
    static let moneyWash = dynamic(light: 0xFFF3CF, dark: 0x241D08)
    static let moneyRule = dynamic(light: 0xF0C95A, dark: 0x7A6212)
    /// Warn and crit are for alerts and nearly-full capacities only.
    static let warn      = dynamic(light: 0xC8500C, dark: 0xFF7D45)
    static let crit      = dynamic(light: 0xB42318, dark: 0xFF5C5C)
    static let onChip    = dynamic(light: 0xFFFFFF, dark: 0x0B1310)

    /// Capacity fill: the meaning colour until 75 %, warn to 90 %, crit above.
    static func capacityFill(fraction: Double, accent: Color) -> Color {
        switch fraction {
        case ..<0.75: return accent
        case ..<0.90: return warn
        default:      return crit
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

extension Font {
    /// The numerals-and-labels voice. Sentences stay in the default system font.
    static func mono(_ size: CGFloat, _ weight: Font.Weight = .regular) -> Font {
        .system(size: size, weight: weight, design: .monospaced).monospacedDigit()
    }
}

extension View {
    /// Small letter-spaced uppercase label in `ink3`.
    func monoLabel() -> some View {
        font(.mono(9.5, .semibold))
            .tracking(1.1)
            .textCase(.uppercase)
            .foregroundStyle(Palette.ink3)
    }
}
