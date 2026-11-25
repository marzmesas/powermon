import SwiftUI

/// A panel whose whole header row is the button. Collapsed it still carries a
/// one-line summary, so folding it away never hides what matters.
struct CollapsibleCard<Summary: View, Content: View>: View {
    let title: String
    @Binding var isExpanded: Bool
    @ViewBuilder let summary: Summary
    @ViewBuilder let content: Content
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                withAnimation(reduceMotion ? nil : .easeOut(duration: 0.15)) { isExpanded.toggle() }
            } label: {
                HStack(spacing: 10) {
                    Text(title).monoLabel().fixedSize()
                    if !isExpanded { summary.lineLimit(1) }
                    Spacer(minLength: 4)
                    Image(systemName: "chevron.right")
                        .font(.system(size: 9, weight: .bold))
                        .foregroundStyle(Palette.ink3)
                        .rotationEffect(.degrees(isExpanded ? 90 : 0))
                }
                .padding(10)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(title)
            .accessibilityValue(isExpanded ? "expanded" : "collapsed")
            .accessibilityHint("Shows or hides the details")

            if isExpanded {
                Rectangle().fill(Palette.rule).frame(height: 1)
                content.padding(10)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Palette.panel, in: RoundedRectangle(cornerRadius: 6))
        .overlay(RoundedRectangle(cornerRadius: 6).stroke(Palette.rule, lineWidth: 1))
    }
}
