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
        .frame(width: 320)
        .background(Palette.ground)
        .onAppear { poller.popoverIsOpen = true }
        .onDisappear { poller.popoverIsOpen = false }
    }

    private var status: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let snapshot = poller.snapshot {
                header(snapshot)
                banner
                // Dim, don't blank: hold the last render while degraded.
                content(snapshot)
                    .opacity(poller.state.showsLastValue ? 1 : 0.55)
            } else {
                placeholder
            }
            Rectangle().fill(Palette.rule).frame(height: 1)
            footer
        }
    }

    // MARK: - Sections

    private var state: PopoverState {
        PopoverState(snapshot: poller.snapshot, connection: poller.state)
    }

    private func header(_ snapshot: Snapshot) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                WatcherMascot(side: 28)
                Text(snapshot.meta.host)
                    .font(.mono(14, .bold))
                    .foregroundStyle(Palette.ink)
                    .lineLimit(1)
                    .truncationMode(.tail)
                StateChip(state: state)
                Spacer(minLength: 0)
            }
            ForEach((snapshot.alerts ?? []).filter { $0.kind != "idle" }, id: \.kind) { alert in
                HStack(spacing: 5) {
                    Circle().fill(Palette.warn).frame(width: 6, height: 6)
                    Text(alert.message)
                        .font(.system(size: 11))
                        .foregroundStyle(Palette.ink2)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
            }
            Text("up \(Format.uptime(snapshot.meta.uptimeS)) · CPU \(snapshot.power.isMeasured ? "measured" : "estimated")")
                .font(.mono(10))
                .foregroundStyle(Palette.ink3)
        }
    }

    @ViewBuilder
    private var banner: some View {
        StatusBanner(
            state: poller.state,
            host: settings.serverURL?.host,
            mac: settings.knownMac,
            onRetry: { poller.refreshNow() },
            onSettings: { showingSettings = true }
        )
    }

    private func content(_ snapshot: Snapshot) -> some View {
        // One watt scale for the gauge and the bars: above everything the
        // snapshot says the machine has drawn.
        let peaks = [snapshot.totals.today.peakW, snapshot.totals.month.peakW, snapshot.totals.all?.peakW]
        let scaleMax = Format.niceMax(above: ([snapshot.power.totalW] + peaks.compactMap { $0 }).max() ?? 0)

        return VStack(alignment: .leading, spacing: 10) {
            HeroView(snapshot: snapshot, state: state, connection: poller.state, scaleMax: scaleMax)

            card {
                WhereItGoesView(rows: wattRows(snapshot), scaleMax: scaleMax)
                Rectangle().fill(Palette.rule).frame(height: 1)
                InstrumentStrip(items: instruments(snapshot))
                if snapshot.gpu.present, !snapshot.gpu.procs.isEmpty {
                    Rectangle().fill(Palette.rule).frame(height: 1)
                    ProcessListView(procs: snapshot.gpu.procs)
                }
            }

            card { TotalsView(totals: snapshot.totals, meta: snapshot.meta) }
        }
    }

    private func card(@ViewBuilder _ content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 10) { content() }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Palette.panel, in: RoundedRectangle(cornerRadius: 6))
            .overlay(RoundedRectangle(cornerRadius: 6).stroke(Palette.rule, lineWidth: 1))
    }

    private func wattRows(_ snapshot: Snapshot) -> [WhereItGoesView.Row] {
        // A failed GPU read shows "—", not zeros, and leaves the rest unknown.
        let gpuFailed = snapshot.gpu.error
        var rows: [WhereItGoesView.Row] = []
        if snapshot.gpu.present {
            rows.append(.init(label: "GPU", watts: gpuFailed ? nil : snapshot.power.gpuW, color: Palette.gpu))
        }
        rows.append(.init(label: "CPU", watts: snapshot.power.cpuW, color: Palette.cpu))
        rows.append(.init(label: "REST", watts: gpuFailed ? nil : snapshot.power.restW, color: Palette.rest))
        return rows
    }

    private func instruments(_ snapshot: Snapshot) -> [InstrumentStrip.Item] {
        var items: [InstrumentStrip.Item] = []
        if snapshot.gpu.present {
            items.append(.init(label: "GPU temp", value: snapshot.gpu.temp.map { "\(Int($0.rounded()))" } ?? Format.dash, unit: "°C"))
        }
        items.append(.init(label: "CPU temp", value: snapshot.cpu.temp.map { "\(Int($0.rounded()))" } ?? Format.dash, unit: "°C"))
        if snapshot.gpu.present {
            let used = snapshot.gpu.memUsed.map { $0 / 1024 }, total = snapshot.gpu.memTotal.map { $0 / 1024 }
            items.append(.init(
                label: "VRAM", value: used.map { String(format: "%.1f", $0) } ?? Format.dash,
                unit: total.map { String(format: "/ %.0f GiB", $0) } ?? "GiB",
                fraction: Format.fraction(used, of: total), accent: Palette.gpu, hasBar: true))
        }
        items.append(.init(
            label: "RAM", value: String(format: "%.1f", snapshot.mem.usedGib),
            unit: String(format: "/ %.0f GiB", snapshot.mem.totalGib),
            fraction: Format.fraction(snapshot.mem.usedGib, of: snapshot.mem.totalGib),
            accent: Palette.rest, hasBar: true))
        return items
    }

    private var placeholder: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(poller.state == .connecting ? "Connecting…" : "Not connected")
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(Palette.ink)
            banner
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var footer: some View {
        HStack(spacing: 14) {
            Button("Open dashboard") {
                if let url = settings.serverURL { NSWorkspace.shared.open(url) }
            }
            .disabled(settings.serverURL == nil)
            Spacer()
            Button("Settings") { showingSettings = true }
                .keyboardShortcut(",", modifiers: .command)
            Button("Quit") { NSApplication.shared.terminate(nil) }
        }
        .buttonStyle(TextButtonStyle())
    }
}

/// The state in one word. Solid for working, outlined for idle, solid warn for
/// an alert, solid grey when there is nothing current to show.
struct StateChip: View {
    let state: PopoverState

    var body: some View {
        let outlined = state == .idle
        Text(state.word)
            .font(.mono(9.5, .bold))
            .tracking(1.1)
            .textCase(.uppercase)
            .lineLimit(1)
            .foregroundStyle(outlined ? Palette.power : Palette.onChip)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(outlined ? Color.clear : fill, in: RoundedRectangle(cornerRadius: 3))
            .overlay(RoundedRectangle(cornerRadius: 3)
                .stroke(Palette.power, lineWidth: outlined ? 1.5 : 0))
            .accessibilityLabel("State: \(state.word)")
    }

    private var fill: Color {
        switch state {
        case .working, .idle: return Palette.power
        case .alert: return Palette.warn
        case .noData, .notAuthorised: return Palette.ink3
        }
    }
}

/// Small mono uppercase text buttons with a visible keyboard focus ring.
struct TextButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        Label(configuration: configuration)
    }

    private struct Label: View {
        let configuration: ButtonStyleConfiguration
        @Environment(\.isEnabled) private var enabled
        @Environment(\.isFocused) private var focused

        var body: some View {
            configuration.label
                .font(.mono(10, .semibold))
                .tracking(1)
                .textCase(.uppercase)
                .foregroundStyle(enabled ? Palette.ink2 : Palette.ink3.opacity(0.6))
                .opacity(configuration.isPressed ? 0.6 : 1)
                .padding(.horizontal, 3)
                .padding(.vertical, 2)
                .overlay(RoundedRectangle(cornerRadius: 3)
                    .stroke(Palette.ink, lineWidth: focused ? 1.5 : 0))
                .focusable()
        }
    }
}
