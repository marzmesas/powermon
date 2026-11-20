import Foundation
import UserNotifications

/// Edge detector for the server's idle-draw alert. Pure, so it can be tested
/// without the notification centre.
struct AlertEdgeDetector {
    enum Transition: Equatable {
        case raised, cleared
    }

    /// Absent until the first snapshot, so an alert already active at launch
    /// counts as raised, once.
    private var last: Bool?

    mutating func observe(active: Bool) -> Transition? {
        defer { last = active }
        switch (last, active) {
        case (nil, true), (false, true): return .raised
        case (true, false):              return .cleared
        default:                         return nil
        }
    }
}

/// Thin wrapper over UNUserNotificationCenter. Authorization is requested the
/// first time something is about to be posted; if denied, nothing happens.
enum AlertNotifier {
    static func post(_ transition: AlertEdgeDetector.Transition, snapshot: Snapshot) {
        // current() crashes without a bundle, and tests must not pop banners.
        guard Bundle.main.bundleIdentifier != nil,
              NSClassFromString("XCTestCase") == nil else { return }

        let content = UNMutableNotificationContent()
        let watts = Int(snapshot.power.totalW.rounded())
        switch transition {
        case .raised:
            content.title = "Powermon: idle but drawing power"
            content.body = "\(snapshot.meta.host) is drawing \(watts) W at the wall."
        case .cleared:
            content.title = "Powermon: back to normal"
            content.body = "\(snapshot.meta.host) is at \(watts) W."
        }
        content.sound = .default

        let center = UNUserNotificationCenter.current()
        center.requestAuthorization(options: [.alert, .sound]) { granted, _ in
            guard granted else { return }
            center.add(UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil))
        }
    }
}
