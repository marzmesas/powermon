import AppKit
import Foundation

/// Owns the polling loop and the only mutable state in the app. Views observe
/// this; the snapshot is replaced wholesale, never partially mutated.
@MainActor
final class Poller: ObservableObject {
    @Published private(set) var snapshot: Snapshot?
    @Published private(set) var state: ConnectionState = .connecting

    /// Poll faster while the user is looking at the popover.
    var popoverIsOpen = false {
        didSet { if popoverIsOpen != oldValue { refreshNow() } }
    }

    private let settings: AppSettings
    private let session: URLSession
    private var task: Task<Void, Never>?
    private var lastSuccess: Date?
    /// Server-side sample timestamp, and when we locally noticed it change.
    /// Comparing the server's clock to itself sidesteps clock skew entirely.
    private var lastServerTs: Double?
    private var lastServerTsChangedAt: Date?
    private var consecutiveFailures = 0

    /// 5 s → 10 s → 30 s → 60 s. A server that is switched off should not mean a
    /// request every 5 s forever.
    private let backoff: [Double] = [5, 10, 30, 60]
    /// Server-side sampling is considered wedged past this, matching /healthz.
    private let stallThreshold: TimeInterval

    init(
        settings: AppSettings = .shared,
        session: URLSession = .shared,
        stallThreshold: TimeInterval = 30
    ) {
        self.settings = settings
        self.session = session
        self.stallThreshold = stallThreshold
        observeSleepWake()
        observeSettingsChanges()
        start()
    }

    // MARK: - Loop

    func start() {
        task?.cancel()
        task = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                await self.tick()
                let delay = self.nextDelay()
                try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
            }
        }
    }

    func stop() {
        task?.cancel()
        task = nil
    }

    /// Fetch immediately rather than waiting out the current sleep.
    func refreshNow() {
        start()
    }

    private func nextDelay() -> Double {
        if consecutiveFailures > 0 {
            return backoff[min(consecutiveFailures - 1, backoff.count - 1)]
        }
        // Faster than the server's own sample period only returns duplicates.
        let floor = snapshot?.meta.interval ?? 2
        return popoverIsOpen ? floor : max(settings.refreshInterval, floor)
    }

    private func tick() async {
        guard let url = settings.serverURL else {
            state = .unreachable("No server URL set")
            return
        }

        let client = PowermonClient(baseURL: url, token: settings.token, session: session)
        do {
            let fresh = try await client.fetchNow()
            apply(fresh)
        } catch {
            fail(with: error)
        }
    }

    private func apply(_ fresh: Snapshot) {
        let now = Date()
        snapshot = fresh
        lastSuccess = now
        consecutiveFailures = 0

        if fresh.ts != lastServerTs {
            lastServerTs = fresh.ts
            lastServerTsChangedAt = now
            state = .live
        } else if let since = lastServerTsChangedAt,
                  now.timeIntervalSince(since) > stallThreshold {
            // Server answers, but it is handing back the same sample: the HTTP
            // thread is alive and the sampler is not.
            state = .samplerStalled
        } else {
            state = .live
        }
    }

    private func fail(with error: Error) {
        consecutiveFailures += 1

        if let clientError = error as? ClientError, clientError == .unauthorized {
            state = .unauthorized
            return
        }

        let reason = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
        // One dropped poll dims the UI; a sustained outage blanks the number.
        if let last = lastSuccess,
           Date().timeIntervalSince(last) < 3 * max(settings.refreshInterval, 2) {
            state = .stale(since: last)
        } else {
            state = .unreachable(reason)
        }
    }

    /// A corrected URL or token should take effect now, not after the backoff.
    private func observeSettingsChanges() {
        NotificationCenter.default.addObserver(
            forName: .powermonSettingsChanged, object: nil, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self else { return }
                self.consecutiveFailures = 0
                self.state = .connecting
                self.start()
            }
        }
    }

    // MARK: - Sleep and wake

    private func observeSleepWake() {
        let center = NSWorkspace.shared.notificationCenter
        for name in [NSWorkspace.willSleepNotification, NSWorkspace.screensDidSleepNotification] {
            center.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                MainActor.assumeIsolated { self?.stop() }
            }
        }
        for name in [NSWorkspace.didWakeNotification, NSWorkspace.screensDidWakeNotification] {
            center.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                MainActor.assumeIsolated {
                    guard let self else { return }
                    // Don't show a value from before the lid closed.
                    self.state = .connecting
                    self.consecutiveFailures = 0
                    self.start()
                }
            }
        }
    }
}
