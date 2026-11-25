import SwiftUI

struct ProcessListView: View {
    let procs: [Snapshot.GPU.Proc]

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("On the GPU").monoLabel()

            ForEach(procs) { proc in
                HStack {
                    Text(proc.name)
                        .font(.system(size: 11))
                        .foregroundStyle(Palette.ink)
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Spacer()
                    Text(Format.mib(proc.memMib))
                        .font(.mono(10))
                        .foregroundStyle(Palette.ink2)
                }
            }
        }
    }
}
