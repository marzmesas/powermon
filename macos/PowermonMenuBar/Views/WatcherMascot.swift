import SwiftUI

/// The app icon's creature in full colour, without the dark tile. Geometry and
/// colours mirror Tools/make-icon.swift (a unit square, y up). The menu bar
/// keeps WatcherGlyph, the one-colour template version.
struct WatcherMascot: View {
    var side: CGFloat = 28

    var body: some View {
        Canvas { context, size in
            let w = size.width
            // Unit square, y up, to a point on the canvas.
            func pt(_ x: CGFloat, _ y: CGFloat) -> CGPoint { CGPoint(x: x * w, y: (1 - y) * w) }
            func disc(_ x: CGFloat, _ y: CGFloat, _ r: CGFloat) -> Path {
                Path(ellipseIn: CGRect(x: (x - r) * w, y: (1 - y - r) * w, width: 2 * r * w, height: 2 * r * w))
            }
            func oval(_ x: CGFloat, _ y: CGFloat, _ width: CGFloat, _ height: CGFloat) -> Path {
                Path(ellipseIn: CGRect(x: x * w, y: (1 - y - height) * w, width: width * w, height: height * w))
            }
            /// Degrees as in the icon script: y up, counter-clockwise positive.
            func arc(_ cx: CGFloat, _ cy: CGFloat, _ r: CGFloat, from: Double, to: Double) -> Path {
                var path = Path()
                let steps = max(2, Int(abs(to - from) / 4))
                for i in 0...steps {
                    let a = (from + (to - from) * Double(i) / Double(steps)) * .pi / 180
                    let p = pt(cx + r * CGFloat(cos(a)), cy + r * CGFloat(sin(a)))
                    i == 0 ? path.move(to: p) : path.addLine(to: p)
                }
                return path
            }
            func stroke(_ path: Path, _ hex: UInt32, _ width: CGFloat, cap: CGLineCap = .round) {
                context.stroke(path, with: .color(Color(hex: hex)),
                               style: StrokeStyle(lineWidth: width * w, lineCap: cap))
            }

            // Antenna: the bolt, small, behind the body.
            let bolt: [CGPoint] = [
                CGPoint(x: 0.56, y: 0.94), CGPoint(x: 0.24, y: 0.46), CGPoint(x: 0.45, y: 0.46),
                CGPoint(x: 0.42, y: 0.06), CGPoint(x: 0.76, y: 0.56), CGPoint(x: 0.55, y: 0.56),
            ]
            var antenna = Path()
            for (i, p) in bolt.enumerated() {
                let q = pt(0.5 + (p.x - 0.5) * 0.30, 0.83 + (p.y - 0.5) * 0.30)
                i == 0 ? antenna.move(to: q) : antenna.addLine(to: q)
            }
            antenna.closeSubpath()
            context.fill(antenna, with: .color(Color(hex: 0xFAB219)))

            // Feet, then the body over them.
            context.fill(oval(0.29, 0.08, 0.16, 0.09), with: .color(Color(hex: 0x0F6E4F)))
            context.fill(oval(0.55, 0.08, 0.16, 0.09), with: .color(Color(hex: 0x0F6E4F)))
            context.fill(disc(0.5, 0.43, 0.31), with: .linearGradient(
                Gradient(colors: [Color(hex: 0x4BE0A6), Color(hex: 0x16936A)]),
                startPoint: pt(0.5, 0.74), endPoint: pt(0.5, 0.12)))

            // The eye: a 240° dial with track, reading, hot stretch, needle, pupil.
            context.fill(disc(0.5, 0.47, 0.205), with: .color(.white))
            stroke(arc(0.5, 0.47, 0.15, from: 210, to: -30), 0xD3DAE5, 0.04)
            stroke(arc(0.5, 0.47, 0.15, from: 210, to: 95), 0x1BAF7A, 0.04)
            stroke(arc(0.5, 0.47, 0.15, from: 95, to: 55), 0xFAB219, 0.04, cap: .butt)
            let a = 55 * Double.pi / 180
            var needle = Path()
            needle.move(to: pt(0.5, 0.47))
            needle.addLine(to: pt(0.5 + 0.15 * CGFloat(cos(a)), 0.47 + 0.15 * CGFloat(sin(a))))
            stroke(needle, 0x0D1118, 0.03)
            context.fill(disc(0.5, 0.47, 0.08), with: .color(Color(hex: 0x0D1118)))
            context.fill(disc(0.475, 0.50, 0.024), with: .color(.white))

            // Smile.
            stroke(arc(0.5, 0.215, 0.04, from: 200, to: 340), 0x0A4A35, 0.024)
        }
        .frame(width: side, height: side)
        .accessibilityHidden(true)
    }
}

extension Color {
    init(hex: UInt32) {
        self.init(.sRGB,
                  red: Double((hex >> 16) & 0xFF) / 255,
                  green: Double((hex >> 8) & 0xFF) / 255,
                  blue: Double(hex & 0xFF) / 255)
    }
}
