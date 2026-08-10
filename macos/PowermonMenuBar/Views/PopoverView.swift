import SwiftUI

struct PopoverView: View {
    @ObservedObject var poller: Poller
    @ObservedObject var settings: AppSettings
    @State private var showingSettings = false

    var body: some View {
        Group {
            if showingSettings {
                SettingsPanelView(settings: settings) { showingSettings = false }
            } else {
                status
            }
        }
        .padding(12)
        .frame(width: 300)
        .onAppear { poller.popoverIsOpen = true }
        .onDisappear { poller.popoverIsOpen = false }
    }

    private var status: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let snapshot = poller.snapshot {
                header(snapshot)
                Divider()
                banner
                // Dim, don't blank: hold the last render while degraded.
                content(snapshot)
                    .opacity(poller.state.showsLastValue ? 1 : 0.55)
            } else {
                placeholder
            }
            Divider()
            footer
        }
    }

    // MARK: - Sections

    private func header(_ snapshot: Snapshot) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(snapshot.meta.host)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(Palette.primary)
                    .lineLimit(1)
                    .truncationMode(.tail)
                Spacer()
                HStack(spacing: 4) {
                    Circle()
                        .fill(snapshot.busy ? Palette.gpu : Palette.muted)
                        .frame(width: 6, height: 6)
                    Text(snapshot.busy ? "training" : "idle")
                        .font(.system(size: 11))
                        .foregroundStyle(Palette.secondary)
                }
            }
            Text("up \(Format.uptime(snapshot.meta.uptimeS)) · CPU \(snapshot.power.isMeasured ? "measured" : "estimated")")
                .font(.system(size: 11))
                .foregroundStyle(Palette.muted)
        }
    }

    @ViewBuilder
    private var banner: some View {
        StatusBanner(
            state: poller.state,
            host: settings.serverURL?.host,
            onRetry: { poller.refreshNow() },
            onSettings: { showingSettings = true }
        )
    }

    private func content(_ snapshot: Snapshot) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HeroView(snapshot: snapshot)
            Divider()
            meters(snapshot)
            Divider()
            TotalsView(totals: snapshot.totals, symbol: snapshot.meta.symbol)

            if snapshot.gpu.present, !snapshot.gpu.procs.isEmpty {
                Divider()
                ProcessListView(procs: snapshot.gpu.procs)
            }
        }
    }

    private func meters(_ snapshot: Snapshot) -> some View {
        // A failed GPU read shows "—", not zeros.
        let gpuFailed = snapshot.gpu.error
        let gpuWatts: Double? = gpuFailed ? nil : snapshot.power.gpuW

        return VStack(spacing: 6) {
            if snapshot.gpu.present {
                MeterRow(
                    label: "GPU",
                    value: Format.watts(gpuWatts),
                    fraction: Format.fraction(gpuWatts, of: snapshot.gpu.limitW),
                    accent: Palette.gpu,
                    trailing: Format.temp(snapshot.gpu.temp)
                )
            }
            MeterRow(
                label: "CPU",
                value: Format.watts(snapshot.power.cpuW),
                fraction: Format.fraction(snapshot.power.cpuW, of: snapshot.cpu.maxW),
                accent: Palette.cpu,
                trailing: Format.temp(snapshot.cpu.temp)
            )
            if snapshot.gpu.present {
                MeterRow(
                    label: "VRAM",
                    value: Format.gib(
                        used: snapshot.gpu.memUsed.map { $0 / 1024 },
                        total: snapshot.gpu.memTotal.map { $0 / 1024 }
                    ),
                    fraction: Format.fraction(snapshot.gpu.memUsed, of: snapshot.gpu.memTotal),
                    accent: Palette.gpu
                )
            }
            MeterRow(
                label: "RAM",
                value: Format.gib(used: snapshot.mem.usedGib, total: snapshot.mem.totalGib),
                fraction: Format.fraction(snapshot.mem.usedGib, of: snapshot.mem.totalGib),
                accent: Palette.other
            )
        }
    }

    private var placeholder: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(poller.state == .connecting ? "Connecting…" : "Not connected")
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(Palette.primary)
            banner
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var footer: some View {
        HStack {
            Button("Open dashboard") {
                if let url = settings.serverURL { NSWorkspace.shared.open(url) }
            }
            .disabled(settings.serverURL == nil)
            Spacer()
            Button("Settings") { showingSettings = true }
                .keyboardShortcut(",", modifiers: .command)
            Button("Quit") { NSApplication.shared.terminate(nil) }
        }
        .font(.system(size: 11))
        .buttonStyle(.link)
    }
}
