import Foundation

enum MenuBarFormat: String, CaseIterable, Identifiable {
    case watts, cost, both

    var id: String { rawValue }

    var label: String {
        switch self {
        case .watts: return "Watts"
        case .cost:  return "Cost per hour"
        case .both:  return "Both"
        }
    }
}

extension Notification.Name {
    /// Posted when the server URL or token changes, so the poller can retry at
    /// once instead of sitting out a backoff of up to 60 s.
    static let powermonSettingsChanged = Notification.Name("powermonSettingsChanged")
}

/// Preferences, backed by UserDefaults. The token is NOT here — see KeychainStore.
final class AppSettings: ObservableObject {
    static let shared = AppSettings()

    @Published var serverURLString: String {
        didSet {
            defaults.set(serverURLString, forKey: "serverURL")
            NotificationCenter.default.post(name: .powermonSettingsChanged, object: nil)
        }
    }
    @Published var format: MenuBarFormat {
        didSet { defaults.set(format.rawValue, forKey: "menuBarFormat") }
    }
    /// Seconds between polls while the popover is closed.
    @Published var refreshInterval: Double {
        didSet { defaults.set(refreshInterval, forKey: "refreshInterval") }
    }

    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        serverURLString = defaults.string(forKey: "serverURL") ?? ""
        format = MenuBarFormat(rawValue: defaults.string(forKey: "menuBarFormat") ?? "") ?? .watts
        refreshInterval = defaults.double(forKey: "refreshInterval") == 0
            ? 5
            : defaults.double(forKey: "refreshInterval")
    }

    var serverURL: URL? {
        let trimmed = serverURLString.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty,
              let url = URL(string: trimmed),
              let scheme = url.scheme, scheme == "http" || scheme == "https",
              url.host != nil
        else { return nil }
        return url
    }

    /// Keychain account key: the host the token belongs to.
    var tokenAccount: String { serverURL?.host ?? "default" }

    var token: String? {
        get { KeychainStore.token(account: tokenAccount) }
        set {
            KeychainStore.setToken(newValue, account: tokenAccount)
            NotificationCenter.default.post(name: .powermonSettingsChanged, object: nil)
        }
    }
}
