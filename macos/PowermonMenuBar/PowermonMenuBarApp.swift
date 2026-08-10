import SwiftUI

@main
struct PowermonMenuBarApp: App {
    @StateObject private var settings = AppSettings.shared
    @StateObject private var poller = Poller()

    var body: some Scene {
        MenuBarExtra {
            PopoverView(poller: poller, settings: settings)
        } label: {
            MenuBarLabel(poller: poller, settings: settings)
        }
        .menuBarExtraStyle(.window)
    }
}
