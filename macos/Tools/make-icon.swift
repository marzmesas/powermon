#!/usr/bin/env swift
// Regenerates the app icon set:  swift Tools/make-icon.swift
// A bolt on the dashboard's series blue, so the app reads as part of powermon.
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
    NSGradient(starting: color(0x3987E5), ending: color(0x1B5FAE))?
        .draw(in: squircle, angle: -90)

    let path = NSBezierPath()
    for (index, point) in bolt.enumerated() {
        let scaled = NSPoint(x: rect.minX + point.x * rect.width,
                             y: rect.minY + point.y * rect.height)
        index == 0 ? path.move(to: scaled) : path.line(to: scaled)
    }
    path.close()
    NSColor.white.setFill()
    path.fill()

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
