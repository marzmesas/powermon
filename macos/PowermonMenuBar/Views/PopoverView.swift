import SwiftUI

struct PopoverView: View {
    @ObservedObject var poller: Poller
    @ObservedObject var settings: AppSettings
    @State private var showingSettings = false
    @AppStorage("popover.whereExpanded") private var storedWhere = false
    @AppStorage("popover.costExpanded") private var storedCost = false
    // Set only by the snapshot tests, so they never write to UserDefaults.
    @State private var forcedWhere: Bool?
    @State private var forcedCost: Bool?

    init(poller: Poller, settings: AppSettings, expanded: (where: Bool, cost: Bool)? = nil) {
        self.poller = poller
        self.settings = settings
        _forcedWhere = State(initialValue: expanded?.where)
        _forcedCost = State(initialValue: expanded?.cost)
    }

    private var whereExpanded: Binding<Bool> {
        Binding(get: { forcedWhere ?? storedWhere },
                set: { if forcedWhere != nil { forcedWhere = $0 } else { storedWhere = $0 } })
    }

    private var costExpanded: Binding<Bool> {
        Binding(get: { forcedCost ?? storedCost },
                set: { if forcedCost != nil { forcedCost = $0 } else { storedCost = $0 } })
    }

    var body: some View {
        Group {
            if showingSettings {
                SettingsPanelView(settings: settings) { showingSettings = false }
                    .frame(width: 336)
            } else {
                status
                    .frame(width: 440)
            }
        }
        .padding(12)
        .background(Palette.ground)
        .onAppear { poller.popoverIsOpen = true }
        .onDisappear { poller.popoverIsOpen = false }
    }

    private var status: some View {
        VStack(alignment: .leading, spacing: 8) {
            header
            if let snapshot = poller.snapshot {
                alertLines(snapshot)
                banner
                // Dim, don't blank: hold the last render while degraded.
                content(snapshot)
                    .opacity(poller.state.showsLastValue ? 1 : 0.55)
            } else {
                placeholder
            }
        }
    }

    // MARK: - Sections

    private var state: PopoverState {
        PopoverState(snapshot: poller.snapshot, connection: poller.state)
    }

    /// One row: who, what state, and the three actions.
    private var header: some View {
        HStack(spacing: 8) {
            WatcherMascot(side: 24)
            if let snapshot = poller.snapshot {
                Text(snapshot.meta.host)
                    .font(.mono(14, .bold))
                    .foregroundStyle(Palette.ink)
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .help("up \(Format.uptime(snapshot.meta.uptimeS)) · CPU \(snapshot.power.isMeasured ? "measured" : "estimated")")
                StateChip(state: state)
            }
            Spacer(minLength: 8)
            HStack(spacing: 10) {
                Button("Dashboard") {
                    if let url = settings.serverURL { NSWorkspace.shared.open(url) }
                }
                .disabled(settings.serverURL == nil)
                Button("Settings") { showingSettings = true }
                    .keyboardShortcut(",", modifiers: .command)
                Button("Quit") { NSApplication.shared.terminate(nil) }
            }
            .buttonStyle(TextButtonStyle())
        }
    }

    @ViewBuilder
    private func alertLines(_ snapshot: Snapshot) -> some View {
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

        let rows = wattRows(snapshot)
        let totals = TotalsView(totals: snapshot.totals, meta: snapshot.meta)

        return VStack(alignment: .leading, spacing: 8) {
            HeroView(snapshot: snapshot, state: state, connection: poller.state, scaleMax: scaleMax)
            CollapsibleCard(title: "Where it goes", isExpanded: whereExpanded) {
                wattsSummary(rows, snapshot)
            } content: {
                VStack(alignment: .leading, spacing: 10) {
                    WhereItGoesView(rows: rows, scaleMax: scaleMax)
                    InstrumentStrip(items: instruments(snapshot))
                }
            }
            CollapsibleCard(title: "Cost", isExpanded: costExpanded) {
                totals.summary.font(.mono(10, .semibold))
            } content: {
                totals
            }
        }
    }

    /// "GPU 29 W · CPU 30 W · REST 45 W", each name in its meaning colour, plus
    /// the worst problem the expanded card would show, so folding hides nothing.
    private func wattsSummary(_ rows: [WhereItGoesView.Row], _ snapshot: Snapshot) -> some View {
        HStack(spacing: 6) {
            ForEach(rows) { row in
                HStack(spacing: 3) {
                    Circle().fill(row.color).frame(width: 6, height: 6)
                    Text("\(row.label) \(Format.watts(row.watts))")
                        .font(.mono(10, .semibold))
                        .foregroundStyle(Palette.ink)
                }
                .accessibilityElement(children: .combine)
            }
            if let problem = problems(snapshot).first {
                HStack(spacing: 3) {
                    Image(systemName: "exclamationmark.triangle.fill").font(.system(size: 9))
                    Text(problem.text).font(.mono(10, .bold))
                }
                .foregroundStyle(problem.color)
                .accessibilityElement(children: .combine)
                .accessibilityLabel("Warning: \(problem.text)")
            }
        }
        .lineLimit(1)
    }

    /// Capacities at 75 % or more and active temperature alerts, worst first.
    private func problems(_ snapshot: Snapshot) -> [(text: String, color: Color, rank: Double)] {
        var found: [(text: String, color: Color, rank: Double)] = []
        func capacity(_ name: String, _ fraction: Double?) {
            guard let fraction, fraction >= 0.75 else { return }
            found.append(("\(name) \(Int((fraction * 100).rounded())) %",
                          Palette.capacityFill(fraction: fraction, accent: Palette.ink), fraction))
        }
        if snapshot.gpu.present {
            capacity("VRAM", Format.fraction(snapshot.gpu.memUsed, of: snapshot.gpu.memTotal))
        }
        capacity("RAM", Format.fraction(snapshot.mem.usedGib, of: snapshot.mem.totalGib))
        if (snapshot.alerts ?? []).contains(where: { $0.kind.contains("temp") || $0.kind.contains("hot") }) {
            found.append(("TEMP", Palette.warn, 0.8))
        }
        return found.sorted { $0.rank > $1.rank }
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
        func temp(_ label: String, _ value: Double?) -> InstrumentStrip.Item {
            .init(label: label, value: value.map { "\(Int($0.rounded()))" } ?? Format.dash, unit: "°C")
        }
        func memory(_ label: String, used: Double?, total: Double?) -> InstrumentStrip.Item {
            .init(label: label,
                  value: used.map { String(format: "%.1f", $0) } ?? Format.dash,
                  unit: total.map { String(format: "/%.0f G", $0) } ?? "G",
                  fraction: Format.fraction(used, of: total))
        }
        var items: [InstrumentStrip.Item] = []
        if snapshot.gpu.present { items.append(temp("GPU temp", snapshot.gpu.temp)) }
        items.append(temp("CPU temp", snapshot.cpu.temp))
        if snapshot.gpu.present {
            items.append(memory("VRAM", used: snapshot.gpu.memUsed.map { $0 / 1024 },
                                total: snapshot.gpu.memTotal.map { $0 / 1024 }))
        }
        items.append(memory("RAM", used: snapshot.mem.usedGib, total: snapshot.mem.totalGib))
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
