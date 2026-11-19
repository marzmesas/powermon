#!/usr/bin/env swift
// Regenerates the app icon set:  swift Tools/make-icon.swift
// The Watcher: a round green creature whose one eye is a meter dial, with a bolt
// for an antenna. Greens and the yellow are the dashboard's `other` and `warning`.
import AppKit

let outputDir = "PowermonMenuBar/Resources/Assets.xcassets/AppIcon.appiconset"

func color(_ hex: UInt32) -> NSColor {
    NSColor(srgbRed: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255, alpha: 1)
}

/// Bolt outline in a unit square, y up.
let bolt: [CGPoint] = [
    CGPoint(x: 0.56, y: 0.94), CGPoint(x: 0.24, y: 0.46), CGPoint(x: 0.45, y: 0.46),
    CGPoint(x: 0.42, y: 0.06), CGPoint(x: 0.76, y: 0.56), CGPoint(x: 0.55, y: 0.56),
]

func render(size: Int) -> Data {
    let side = CGFloat(size)
    // Draw straight into a bitmap rep: exact pixel dimensions, and none of
    // NSImage.lockFocus's ordering traps.
    let rep = NSBitmapImageRep(
        bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size,
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
        colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0
    )!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)

    // Rounded square, matching the macOS icon shape closely enough at any size.
    let inset = side * 0.06
    let rect = NSRect(x: inset, y: inset, width: side - inset * 2, height: side - inset * 2)
    let squircle = NSBezierPath(roundedRect: rect,
                                xRadius: rect.width * 0.2237,
                                yRadius: rect.width * 0.2237)
    NSGradient(starting: color(0x22302B), ending: color(0x0B1310))?
        .draw(in: squircle, angle: -90)
    squircle.addClip()

    // Everything below draws in a unit square, y up.
    let unit = NSAffineTransform()
    unit.translateX(by: rect.minX, yBy: rect.minY)
    unit.scale(by: rect.width)
    unit.concat()

    func circle(_ x: CGFloat, _ y: CGFloat, _ r: CGFloat) -> NSBezierPath {
        NSBezierPath(ovalIn: NSRect(x: x - r, y: y - r, width: r * 2, height: r * 2))
    }
    func arc(_ x: CGFloat, _ y: CGFloat, _ r: CGFloat, from: CGFloat, to: CGFloat, width: CGFloat,
             clockwise: Bool = true, cap: NSBezierPath.LineCapStyle = .round) -> NSBezierPath {
        let path = NSBezierPath()
        path.appendArc(withCenter: NSPoint(x: x, y: y), radius: r,
                       startAngle: from, endAngle: to, clockwise: clockwise)
        path.lineWidth = width
        path.lineCapStyle = cap
        return path
    }

    // Antenna: the bolt, small, behind the body.
    let antenna = NSBezierPath()
    for (index, point) in bolt.enumerated() {
        let scaled = NSPoint(x: 0.5 + (point.x - 0.5) * 0.30, y: 0.83 + (point.y - 0.5) * 0.30)
        index == 0 ? antenna.move(to: scaled) : antenna.line(to: scaled)
    }
    antenna.close()
    color(0xFAB219).setFill()
    antenna.fill()

    // Feet, then the body over them.
    color(0x0F6E4F).setFill()
    NSBezierPath(ovalIn: NSRect(x: 0.29, y: 0.08, width: 0.16, height: 0.09)).fill()
    NSBezierPath(ovalIn: NSRect(x: 0.55, y: 0.08, width: 0.16, height: 0.09)).fill()
    NSGradient(starting: color(0x4BE0A6), ending: color(0x16936A))?
        .draw(in: circle(0.5, 0.43, 0.31), angle: -90)

    // The eye is a 240° dial: track, reading, a hot last stretch, needle, pupil.
    let eye = NSPoint(x: 0.5, y: 0.47)
    NSColor.white.setFill()
    circle(eye.x, eye.y, 0.205).fill()
    color(0xD3DAE5).setStroke()
    arc(eye.x, eye.y, 0.15, from: 210, to: -30, width: 0.04).stroke()
    color(0x1BAF7A).setStroke()
    arc(eye.x, eye.y, 0.15, from: 210, to: 95, width: 0.04).stroke()
    color(0xFAB219).setStroke()
    arc(eye.x, eye.y, 0.15, from: 95, to: 55, width: 0.04, cap: .butt).stroke()
    let angle = 55 * CGFloat.pi / 180
    let needle = NSBezierPath()
    needle.move(to: eye)
    needle.line(to: NSPoint(x: eye.x + cos(angle) * 0.15, y: eye.y + sin(angle) * 0.15))
    needle.lineWidth = 0.03
    needle.lineCapStyle = .round
    color(0x0D1118).setStroke()
    needle.stroke()
    color(0x0D1118).setFill()
    circle(eye.x, eye.y, 0.08).fill()
    NSColor.white.setFill()
    circle(0.475, 0.50, 0.024).fill()

    // Smile.
    color(0x0A4A35).setStroke()
    arc(0.5, 0.215, 0.04, from: 200, to: 340, width: 0.024, clockwise: false).stroke()

    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

// (filename, pixel size) — the sizes macOS actually asks for.
let outputs: [(String, Int)] = [
    ("icon_16x16.png", 16), ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32), ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128), ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256), ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512), ("icon_512x512@2x.png", 1024),
]

try? FileManager.default.createDirectory(atPath: outputDir, withIntermediateDirectories: true)
for (name, size) in outputs {
    try! render(size: size).write(to: URL(fileURLWithPath: "\(outputDir)/\(name)"))
}

let entries = outputs.map { name, size -> String in
    let scale = name.contains("@2x") ? 2 : 1
    let point = size / scale
    return """
        {
          "filename" : "\(name)",
          "idiom" : "mac",
          "scale" : "\(scale)x",
          "size" : "\(point)x\(point)"
        }
    """
}
let contents = """
{
  "images" : [
\(entries.joined(separator: ",\n"))
  ],
  "info" : { "author" : "xcode", "version" : 1 }
}

"""
try! contents.write(toFile: "\(outputDir)/Contents.json", atomically: true, encoding: .utf8)
print("wrote \(outputs.count) icons to \(outputDir)")
