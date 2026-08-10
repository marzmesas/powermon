import SwiftUI

/// Shape only — no axes, no gridlines, no labels. The hero number shows value.
struct Sparkline: View {
    let values: [Double]
    var color: Color = Palette.gpu

    var body: some View {
        GeometryReader { geo in
            let points = points(in: geo.size)
            if points.count > 1 {
                ZStack {
                    area(points, height: geo.size.height)
                        .fill(color.opacity(0.12))
                    line(points)
                        .stroke(color, style: StrokeStyle(lineWidth: 2, lineJoin: .round))
                    if let last = points.last {
                        Circle()
                            .fill(color)
                            .frame(width: 4, height: 4)
                            .position(last)
                    }
                }
            }
        }
    }

    private func points(in size: CGSize) -> [CGPoint] {
        guard values.count > 1 else { return [] }
        let low = values.min() ?? 0
        let high = values.max() ?? 1
        // Never stretch the range to fit: an idle box wobbling by 1 W would be
        // drawn as violent spikes. Below a floor of 15 % of peak draw, the line
        // stays visibly flat, which is the truth.
        let midpoint = (low + high) / 2
        let span = max(high - low, max(high * 0.15, 1))
        let floor = midpoint - span / 2
        let stepX = size.width / CGFloat(values.count - 1)

        return values.enumerated().map { index, value in
            let normalised = (value - floor) / span
            // Inset by the stroke width so the line is not clipped at the edges.
            let usable = size.height - 2
            return CGPoint(x: CGFloat(index) * stepX, y: 1 + usable * (1 - normalised))
        }
    }

    private func line(_ points: [CGPoint]) -> Path {
        var path = Path()
        path.move(to: points[0])
        for point in points.dropFirst() { path.addLine(to: point) }
        return path
    }

    private func area(_ points: [CGPoint], height: CGFloat) -> Path {
        var path = line(points)
        path.addLine(to: CGPoint(x: points[points.count - 1].x, y: height))
        path.addLine(to: CGPoint(x: points[0].x, y: height))
        path.closeSubpath()
        return path
    }
}
