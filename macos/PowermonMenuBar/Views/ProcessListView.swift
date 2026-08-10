import SwiftUI

struct ProcessListView: View {
    let procs: [Snapshot.GPU.Proc]

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("On the GPU")
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(Palette.secondary)

            ForEach(procs) { proc in
                HStack {
                    Text(proc.name)
                        .font(.system(size: 11))
                        .foregroundStyle(Palette.primary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Spacer()
                    Text(Format.mib(proc.memMib))
                        .font(.system(size: 11).monospacedDigit())
                        .foregroundStyle(Palette.secondary)
                }
            }
        }
    }
}
