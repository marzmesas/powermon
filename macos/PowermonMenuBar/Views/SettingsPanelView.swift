import ServiceManagement
import SwiftUI

/// Settings live inside the panel rather than in a window of their own: a menu
/// bar app has one surface, and a separate window either overlaps the panel or
/// opens somewhere unrelated to where you clicked.
struct SettingsPanelView: View {
    @ObservedObject var settings: AppSettings
    let onBack: () -> Void

    // Edited locally and committed on submit or on the way out. Writing on
    // every keystroke would restart the poller once per character.
    @State private var urlDraft = ""
    @State private var tokenDraft = ""

    @State private var testResult: TestResult?
    @State private var testing = false
    @State private var launchAtLogin = SMAppService.mainApp.status == .enabled
    @State private var launchError: String?

    private enum TestResult: Equatable {
        case success(String)
        case failure(String)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            header
            Divider()

            field("Server URL") {
                TextField("http://marzmesas-trainbox:8787", text: $urlDraft)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(size: 11))
                    .onSubmit(commit)
            }
            if !urlDraft.isEmpty, parsedURL == nil {
                note("Needs a full http:// URL with a host.", color: Palette.critical)
            }

            field("Token") {
                HStack(spacing: 4) {
                    SecureField("Only needed off-loopback", text: $tokenDraft)
                        .textFieldStyle(.roundedBorder)
                        .font(.system(size: 11))
                        .onSubmit(commit)
                    // Tokens get pasted, not typed — and this keeps working
                    // even if the panel is awkward about keyboard focus.
                    Button("Paste") {
                        if let clipboard = NSPasteboard.general.string(forType: .string) {
                            tokenDraft = clipboard.trimmingCharacters(in: .whitespacesAndNewlines)
                            commit()
                        }
                    }
                    .font(.system(size: 10))
                }
            }
            note("Stored in your Keychain.", color: Palette.muted)

            testRow
            Divider()

            pickerRow("Menu bar") {
                Picker("", selection: $settings.format) {
                    ForEach(MenuBarFormat.allCases) { Text($0.label).tag($0) }
                }
            }
            pickerRow("Refresh") {
                Picker("", selection: $settings.refreshInterval) {
                    Text("2 s").tag(2.0)
                    Text("5 s").tag(5.0)
                    Text("10 s").tag(10.0)
                }
            }

            Toggle("Launch at login", isOn: $launchAtLogin)
                .font(.system(size: 11))
                .toggleStyle(.switch)
                .controlSize(.mini)
                .onChange(of: launchAtLogin) { setLaunchAtLogin($0) }
            if let launchError {
                note(launchError, color: Palette.critical)
            }
        }
        .onAppear {
            urlDraft = settings.serverURLString
            tokenDraft = settings.token ?? ""
        }
        .onDisappear(perform: commit)
    }

    // MARK: - Pieces

    private var header: some View {
        HStack(spacing: 6) {
            Button {
                commit()
                onBack()
            } label: {
                HStack(spacing: 2) {
                    Image(systemName: "chevron.left")
                    Text("Back")
                }
                .font(.system(size: 11))
            }
            .buttonStyle(.link)
            .keyboardShortcut(.escape, modifiers: [])

            Spacer()
            Text("Settings")
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(Palette.primary)
            Spacer()
            // Balances the back button so the title sits centred.
            Text("Back").font(.system(size: 11)).opacity(0)
        }
    }

    private func field(_ label: String, @ViewBuilder content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label)
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(Palette.secondary)
            content()
        }
    }

    private func note(_ text: String, color: Color) -> some View {
        Text(text)
            .font(.system(size: 10))
            .foregroundStyle(color)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func pickerRow(_ label: String, @ViewBuilder content: () -> some View) -> some View {
        HStack {
            Text(label)
                .font(.system(size: 11))
                .foregroundStyle(Palette.secondary)
            Spacer()
            content()
                .labelsHidden()
                .font(.system(size: 11))
                .frame(width: 110)
        }
    }

    private var testRow: some View {
        HStack(spacing: 8) {
            Button(testing ? "Testing…" : "Test connection") { test() }
                .font(.system(size: 11))
                .disabled(testing || parsedURL == nil)

            switch testResult {
            case .success(let message):
                Label(message, systemImage: "checkmark.circle.fill")
                    .font(.system(size: 10))
                    .foregroundStyle(Palette.good)
                    .lineLimit(1)
            case .failure(let message):
                Label(message, systemImage: "xmark.circle.fill")
                    .font(.system(size: 10))
                    .foregroundStyle(Palette.critical)
                    .lineLimit(2)
            case nil:
                EmptyView()
            }
        }
    }

    // MARK: - Actions

    /// Mirrors AppSettings.serverURL, but on the draft the user is editing.
    private var parsedURL: URL? {
        let trimmed = urlDraft.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty,
              let url = URL(string: trimmed),
              let scheme = url.scheme, scheme == "http" || scheme == "https",
              url.host != nil
        else { return nil }
        return url
    }

    private func commit() {
        if settings.serverURLString != urlDraft {
            settings.serverURLString = urlDraft
        }
        if (settings.token ?? "") != tokenDraft {
            settings.token = tokenDraft
        }
    }

    private func test() {
        commit()
        guard let url = parsedURL else { return }
        testing = true
        testResult = nil

        Task {
            let client = PowermonClient(baseURL: url, token: tokenDraft.isEmpty ? nil : tokenDraft)
            do {
                let snapshot = try await client.fetchNow()
                testResult = .success("Connected to \(snapshot.meta.host)")
            } catch let error as ClientError {
                testResult = .failure(error.errorDescription ?? "Failed")
            } catch {
                testResult = .failure(error.localizedDescription)
            }
            testing = false
        }
    }

    private func setLaunchAtLogin(_ enabled: Bool) {
        launchError = nil
        do {
            if enabled {
                try SMAppService.mainApp.register()
            } else {
                try SMAppService.mainApp.unregister()
            }
        } catch {
            // Unsigned builds cannot register; say so instead of silently
            // leaving the toggle on.
            launchError = "Couldn't change this: \(error.localizedDescription)"
            launchAtLogin = SMAppService.mainApp.status == .enabled
        }
    }
}
