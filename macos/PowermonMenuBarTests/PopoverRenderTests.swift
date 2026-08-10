import SwiftUI
import XCTest
@testable import Powermon

/// Renders the panel for the awkward fixtures in both appearances. Asserts it
/// is not blank, and writes PNGs so the result can actually be looked at —
/// "it compiled" says nothing about whether a GPU-less box renders empty rows.
@MainActor
final class PopoverRenderTests: XCTestCase {
    private var suite: String!
    private var defaults: UserDefaults!

    override func setUp() {
        super.setUp()
        suite = "powermon.render.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suite)
        defaults.set("http://stub.invalid:8787", forKey: "serverURL")
        defaults.set(2.0, forKey: "refreshInterval")
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suite)
        super.tearDown()
    }

    func testRendersEveryInterestingFixtureInBothAppearances() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("powermon-snapshots", isDirectory: true)
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)

        for fixture in ["sample-now", "gpu-absent", "gpu-error", "no-sensors", "fresh-install"] {
            let poller = try await livePoller(serving: fixture)
            let settings = AppSettings(defaults: defaults)

            for (name, appearance) in [("light", NSAppearance.Name.aqua),
                                       ("dark", NSAppearance.Name.darkAqua)] {
                let image = render(
                    PopoverView(poller: poller, settings: settings),
                    appearance: appearance
                )
                XCTAssertGreaterThan(image.size.width, 0, "\(fixture)/\(name) has no width")
                XCTAssertGreaterThan(
                    distinctColours(in: image), 8,
                    "\(fixture)/\(name) rendered essentially blank"
                )
                let url = directory.appendingPathComponent("\(fixture)-\(name).png")
                try png(from: image).write(to: url)
            }
        }
        print("SNAPSHOTS: \(directory.path)")
    }

    // MARK: - Helpers

    private func livePoller(serving fixture: String) async throws -> Poller {
        let url = try XCTUnwrap(Bundle(for: Self.self).url(forResource: fixture, withExtension: "json"))
        StubURLProtocol.status = 200
        StubURLProtocol.body = try Data(contentsOf: url)

        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubURLProtocol.self]
        let poller = Poller(
            settings: AppSettings(defaults: defaults),
            session: URLSession(configuration: config)
        )

        let deadline = Date().addingTimeInterval(5)
        while Date() < deadline, poller.snapshot == nil {
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        XCTAssertNotNil(poller.snapshot, "\(fixture) never produced a snapshot")
        poller.stop()
        return poller
    }

    private func render(_ view: some View, appearance: NSAppearance.Name) -> NSImage {
        // The real panel sits on system chrome. Without a background here, white
        // dark-mode text renders invisibly on nothing and the snapshot lies.
        let backed = ZStack {
            Color(nsColor: .windowBackgroundColor)
            view
        }
        let hosting = NSHostingView(rootView: backed)
        hosting.appearance = NSAppearance(named: appearance)
        hosting.frame = NSRect(origin: .zero, size: hosting.fittingSize)
        hosting.layoutSubtreeIfNeeded()

        let image = NSImage(size: hosting.bounds.size)
        if let rep = hosting.bitmapImageRepForCachingDisplay(in: hosting.bounds) {
            hosting.cacheDisplay(in: hosting.bounds, to: rep)
            image.addRepresentation(rep)
        }
        return image
    }

    private func png(from image: NSImage) throws -> Data {
        let rep = try XCTUnwrap(image.representations.first as? NSBitmapImageRep)
        return try XCTUnwrap(rep.representation(using: .png, properties: [:]))
    }

    /// A blank render collapses to one or two colours; a real one has many.
    private func distinctColours(in image: NSImage) -> Int {
        guard let rep = image.representations.first as? NSBitmapImageRep else { return 0 }
        var seen = Set<UInt32>()
        for x in stride(from: 0, to: rep.pixelsWide, by: 4) {
            for y in stride(from: 0, to: rep.pixelsHigh, by: 4) {
                guard let colour = rep.colorAt(x: x, y: y) else { continue }
                let packed = UInt32(colour.redComponent * 255) << 16
                    | UInt32(colour.greenComponent * 255) << 8
                    | UInt32(colour.blueComponent * 255)
                seen.insert(packed)
            }
        }
        return seen.count
    }
}
