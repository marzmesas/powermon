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

    /// The states the redesign has to get right, as 2x PNGs in /tmp so they can
    /// be looked at. Write failures are ignored: the snapshots are a convenience.
    func testWritesStateSnapshots() async throws {
        let directory = URL(fileURLWithPath: "/tmp/claude-popover", isDirectory: true)
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let settings = AppSettings(defaults: defaults)

        func write(_ name: String, _ poller: Poller, expanded: Bool = false) {
            for (mode, appearance) in [("light", NSAppearance.Name.aqua), ("dark", NSAppearance.Name.darkAqua)] {
                let view = PopoverView(poller: poller, settings: settings, expanded: (expanded, expanded))
                let image = render(view, appearance: appearance, scale: 2)
                let label = "\(name)-\(mode)"
                XCTAssertGreaterThan(distinctColours(in: image), 8, "\(label) rendered blank")
                print("SIZE \(label): \(Int(image.size.width)) x \(Int(image.size.height))")
                // Collapsed is short; expanded is the most it may grow to. An alert line
                // or the unreachable banner adds a little more room.
                let extra: CGFloat = name == "unreachable" ? 80 : name == "idle-alert" ? 40 : 0
                let ceiling: CGFloat = (expanded ? 440 : 250) + extra
                XCTAssertLessThanOrEqual(image.size.height, ceiling, "\(label) is too tall")
                try? png(from: image).write(to: directory.appendingPathComponent("\(label).png"))
            }
        }

        let now = Date().timeIntervalSince1970
        let working = try await livePoller(body: payload("sample-now") { json in
            json["busy"] = true
            json["power"] = ["total_w": 475.0, "cpu_w": 74.0, "gpu_w": 319.0, "other_w": 82.0,
                             "cost_per_h": 0.1425, "source": "rapl"]
            var gpu = json["gpu"] as! [String: Any]
            gpu["temp"] = 71.0
            gpu["mem_used"] = 18_227.0
            json["gpu"] = gpu
            json["run"] = ["id": 7, "start": now - 4_000, "seconds": 4_000.0, "kwh": 0.9,
                           "cost": 0.80, "name": "train.py", "running": true]
            json["ts"] = now
            monthly(&json, idleCost: 10.41)
        }, label: "working")
        write("working", working)
        write("working-expanded", working, expanded: true)

        let idle = try await livePoller(body: payload("sample-now") { json in
            var gpu = json["gpu"] as! [String: Any]
            gpu["mem_used"] = 12_000.0   // comfortably under the warn level
            json["gpu"] = gpu
            monthly(&json, idleCost: 10.41)
        }, label: "idle")
        write("idle", idle)
        write("idle-expanded", idle, expanded: true)

        // Worst case for the collapsed row: three-digit watts and a problem marker.
        write("idle-alert", try await livePoller(body: payload("sample-now") { json in
            json["power"] = ["total_w": 160.0, "cpu_w": 40.0, "gpu_w": 60.0, "other_w": 60.0,
                             "cost_per_h": 0.024, "source": "estimated"]
            json["alerts"] = [["kind": "idle", "message": "Idle but drawing 160 W", "since": now - 720],
                              ["kind": "gpu_hot", "message": "GPU at 88 °C"]]
            json["ts"] = now
            monthly(&json, idleCost: 10.41)
        }, label: "idle-alert"))

        // sample-now's VRAM is 94 % full: the warning must show on the folded card.
        write("vram-full", try await livePoller(body: payload("sample-now") { json in
            json["power"] = ["total_w": 319.0, "cpu_w": 74.0, "gpu_w": 319.0, "other_w": 0.0,
                             "cost_per_h": 0.05, "source": "rapl"]
            monthly(&json, idleCost: 10.41)
        }, label: "vram-full"))

        let noGPU = try await livePoller(body: payload("gpu-absent") { monthly(&$0, idleCost: 4.2) }, label: "no-gpu")
        write("no-gpu", noGPU)

        let noTariff = try await livePoller(body: payload("sample-now") { json in
            var meta = json["meta"] as! [String: Any]
            meta["mode"] = "none"
            json["meta"] = meta
            monthly(&json, idleCost: nil)
        }, label: "no-tariff")
        write("no-tariff", noTariff)
        write("no-tariff-expanded", noTariff, expanded: true)

        // Unreachable with a remembered MAC: serve once, then fail until it gives up.
        defaults.set("aa:bb:cc:dd:ee:ff", forKey: "mac.stub.invalid")
        let down = try await livePoller(body: payload("sample-now") { monthly(&$0, idleCost: 10.41) }, label: "unreachable")
        StubURLProtocol.status = 500
        StubURLProtocol.body = Data(#"{"error": "boom"}"#.utf8)
        down.start()
        let deadline = Date().addingTimeInterval(25)
        while Date() < deadline {
            if case .unreachable = down.state { break }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        down.stop()
        write("unreachable", down)

        let settingsImage = render(SettingsPanelView(settings: settings) {}.frame(width: 336).padding(12)
            .background(Palette.ground), appearance: .darkAqua, scale: 2)
        try? png(from: settingsImage).write(to: directory.appendingPathComponent("settings-dark.png"))
    }

    // MARK: - Helpers

    /// A fixture's JSON after `edit`, as the body the stub server returns.
    private func payload(_ fixture: String, edit: (inout [String: Any]) -> Void) throws -> Data {
        let url = try XCTUnwrap(Bundle(for: Self.self).url(forResource: fixture, withExtension: "json"))
        var json = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any])
        edit(&json)
        return try JSONSerialization.data(withJSONObject: json)
    }

    private func monthly(_ json: inout [String: Any], idleCost: Double?) {
        var totals = json["totals"] as! [String: Any]
        var month = totals["month"] as! [String: Any]
        if let idleCost { month["idle_cost"] = idleCost }
        totals["month"] = month
        json["totals"] = totals
    }

    private func livePoller(serving fixture: String) async throws -> Poller {
        let url = try XCTUnwrap(Bundle(for: Self.self).url(forResource: fixture, withExtension: "json"))
        return try await livePoller(body: Data(contentsOf: url), label: fixture)
    }

    private func livePoller(body: Data, label: String) async throws -> Poller {
        StubURLProtocol.status = 200
        StubURLProtocol.body = body

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
        XCTAssertNotNil(poller.snapshot, "\(label) never produced a snapshot")
        poller.stop()
        return poller
    }

    private func render(_ view: some View, appearance: NSAppearance.Name, scale: Int = 1) -> NSImage {
        // The panel paints its own ground, so nothing sits behind it here.
        let hosting = NSHostingView(rootView: view)
        hosting.appearance = NSAppearance(named: appearance)
        hosting.frame = NSRect(origin: .zero, size: hosting.fittingSize)
        hosting.layoutSubtreeIfNeeded()

        let image = NSImage(size: hosting.bounds.size)
        let pixels = (Int(hosting.bounds.width) * scale, Int(hosting.bounds.height) * scale)
        if let rep = NSBitmapImageRep(
            bitmapDataPlanes: nil, pixelsWide: pixels.0, pixelsHigh: pixels.1,
            bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
            colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0
        ) {
            rep.size = hosting.bounds.size
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
