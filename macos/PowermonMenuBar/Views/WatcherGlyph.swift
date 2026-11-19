import AppKit

/// The app icon's mascot as a one-colour menu bar glyph: bolt antenna, round
/// body, the eye and the smile cut out of it. Shapes mirror Tools/make-icon.swift.
enum WatcherGlyph {
    /// Not a template image: the colour carries the connection state.
    static func image(color: NSColor, side: CGFloat = 18) -> NSImage {
        let image = NSImage(size: NSSize(width: side, height: side), flipped: false) { rect in
            guard let context = NSGraphicsContext.current?.cgContext else { return false }
            draw(in: rect, color: color, context: context)
            return true
        }
        image.isTemplate = false
        return image
    }

    static func draw(in rect: CGRect, color: NSColor, context: CGContext) {
        context.saveGState()
        defer { context.restoreGState() }
        // Unit square, y up.
        context.translateBy(x: rect.minX, y: rect.minY)
        context.scaleBy(x: rect.width, y: rect.height)
        context.setFillColor(color.cgColor)
        context.setStrokeColor(color.cgColor)

        func disc(_ x: CGFloat, _ y: CGFloat, _ r: CGFloat) -> CGRect {
            CGRect(x: x - r, y: y - r, width: r * 2, height: r * 2)
        }

        // Antenna.
        let bolt: [CGPoint] = [
            CGPoint(x: 0.56, y: 0.94), CGPoint(x: 0.24, y: 0.46), CGPoint(x: 0.45, y: 0.46),
            CGPoint(x: 0.42, y: 0.06), CGPoint(x: 0.76, y: 0.56), CGPoint(x: 0.55, y: 0.56),
        ]
        context.addLines(between: bolt.map {
            CGPoint(x: 0.5 + ($0.x - 0.5) * 0.42, y: 0.81 + ($0.y - 0.5) * 0.42)
        })
        context.closePath()
        context.fillPath()

        // Feet.
        context.fillEllipse(in: CGRect(x: 0.20, y: 0.00, width: 0.24, height: 0.13))
        context.fillEllipse(in: CGRect(x: 0.56, y: 0.00, width: 0.24, height: 0.13))

        // Body, with the eye and the smile left transparent. Even-odd fill rather
        // than a blend mode: the handler may draw straight into the menu bar.
        let smile = CGMutablePath()
        smile.addArc(center: CGPoint(x: 0.5, y: 0.20), radius: 0.09,
                     startAngle: 205 * .pi / 180, endAngle: 335 * .pi / 180, clockwise: false)
        context.addEllipse(in: disc(0.5, 0.40, 0.36))
        context.addEllipse(in: disc(0.5, 0.46, 0.21))
        context.addPath(smile.copy(strokingWithWidth: 0.05, lineCap: .round, lineJoin: .round, miterLimit: 1))
        context.fillPath(using: .evenOdd)

        // Pupil and needle.
        context.fillEllipse(in: disc(0.5, 0.46, 0.11))
        context.setLineWidth(0.06)
        context.setLineCap(.round)
        context.move(to: CGPoint(x: 0.5, y: 0.46))
        context.addLine(to: CGPoint(x: 0.62, y: 0.62))
        context.strokePath()
    }
}
