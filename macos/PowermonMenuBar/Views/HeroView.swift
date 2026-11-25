import SwiftUI

/// The hero panel: the dial on the left; the big number, the cost rate and one
/// spoken sentence beside it. The top rule and the dial take the state's colour.
struct HeroView: View {
    let snapshot: Snapshot
    let state: PopoverState
    let connection: ConnectionState
    /// The gauge's maximum, shared with the "where it goes" bars.
    let scaleMax: Double

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 10) {
                GaugeView(watts: snapshot.power.totalW, max: scaleMax, accent: accent)
                VStack(alignment: .leading, spacing: 6) {
                    readout
                    sentence
                        .font(.system(size: 12))
                        .foregroundColor(Palette.ink)
                        .lineLimit(3)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            Rectangle().fill(Palette.rule).frame(height: 1)
            CostLine(totals: snapshot.totals, meta: snapshot.meta)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            ZStack {
                Palette.panel
                RadialGradient(colors: [Palette.heroWash, Palette.heroWash.opacity(0)],
                               center: .topLeading, startRadius: 0, endRadius: 240)
            }
        )
        .overlay(alignment: .top) { Rectangle().fill(accent).frame(height: 3) }
        .clipShape(RoundedRectangle(cornerRadius: 6))
        .overlay(RoundedRectangle(cornerRadius: 6).stroke(Palette.rule, lineWidth: 1))
    }

    private var readout: some View {
        HStack(alignment: .firstTextBaseline, spacing: 2) {
            Text(Format.wattsValue(snapshot.power.totalW))
                .font(.system(size: 38, weight: .heavy, design: .monospaced).monospacedDigit())
                .tracking(-1)
                .foregroundStyle(Palette.ink)
                .lineLimit(1)
                .minimumScaleFactor(0.6)
            Text("W")
                .font(.mono(13, .bold))
                .foregroundStyle(accent)
            if snapshot.meta.hasTariff {
                Text(Format.costPerHour(snapshot.power.costPerH, symbol: snapshot.meta.symbol))
                    .font(.mono(12, .semibold))
                    .foregroundStyle(Palette.money)
                    .padding(.leading, 8)
            }
        }
        .accessibilityElement(children: .combine)
    }

    private var accent: Color {
        switch state {
        case .working, .idle: return Palette.power
        case .alert: return Palette.warn
        case .noData, .notAuthorised: return Palette.ink3
        }
    }

    private var sentence: Text {
        Speech.pieces(snapshot: snapshot, state: connection).reduce(Text("")) { text, piece in
            switch piece.style {
            case .plain: return text + Text(piece.text)
            case .watts: return text + Text(piece.text).bold()
            case .money: return text + Text(piece.text).bold().foregroundColor(Palette.money)
            }
        }
    }
}

/// Watts at the wall on a 240° dial, like the web one: track, value arc,
/// needle, and the scale's two ends in numerals.
struct GaugeView: View {
    let watts: Double
    let max: Double
    let accent: Color

    private var fraction: Double { Swift.min(Swift.max(watts / max, 0), 1) }

    var body: some View {
        ZStack {
            DialArc(fraction: 1)
                .stroke(Palette.track, style: StrokeStyle(lineWidth: 7, lineCap: .round))
            DialArc(fraction: fraction)
                .stroke(accent, style: StrokeStyle(lineWidth: 7, lineCap: .round))
            DialNeedle(fraction: fraction)
                .stroke(Palette.ink, style: StrokeStyle(lineWidth: 2, lineCap: .round))
            Circle().fill(Palette.ink).frame(width: 7, height: 7)
                .position(x: 48, y: 50)
            Text("0").monoLabel().position(x: 14, y: 80)
            Text("\(Int(max))").monoLabel().position(x: 82, y: 80)
        }
        .frame(width: 96, height: 88)
        .scaleEffect(84.0 / 96)
        .frame(width: 84, height: 77)
        .animation(.easeOut(duration: 0.4), value: fraction)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Watts at the wall")
        .accessibilityValue("\(Format.wattsValue(watts)) watts on a scale of 0 to \(Int(max))")
    }
}

/// The dial's centre is (48, 50) in a 96 x 88 box; it starts at the lower left
/// (150° clockwise from the right, y down) and sweeps 240°.
private enum Dial {
    static let centre = CGPoint(x: 48, y: 50)
    static let radius: CGFloat = 40
    static func point(_ fraction: Double, _ r: CGFloat) -> CGPoint {
        let a = (150 + 240 * fraction) * .pi / 180
        return CGPoint(x: centre.x + r * CGFloat(cos(a)), y: centre.y + r * CGFloat(sin(a)))
    }
}

private struct DialArc: Shape {
    var fraction: Double
    var animatableData: Double {
        get { fraction }
        set { fraction = newValue }
    }

    func path(in rect: CGRect) -> Path {
        var path = Path()
        let steps = 60
        for i in 0...steps {
            let p = Dial.point(fraction * Double(i) / Double(steps), Dial.radius)
            i == 0 ? path.move(to: p) : path.addLine(to: p)
        }
        return path
    }
}

private struct DialNeedle: Shape {
    var fraction: Double
    var animatableData: Double {
        get { fraction }
        set { fraction = newValue }
    }

    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: Dial.centre)
        path.addLine(to: Dial.point(fraction, Dial.radius - 10))
        return path
    }
}
