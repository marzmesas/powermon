# Powermon — macOS menu bar app

A client of the `powermon` service on the training box. Shows what the machine
is drawing right now in the menu bar; clicking opens a panel with live detail.
It collects nothing itself. Full spec: [`../docs/macos-menubar-app.md`](../docs/macos-menubar-app.md).

## Build

`project.yml` is the source of truth — the `.xcodeproj` is generated and
gitignored.

```sh
brew install xcodegen        # once
cd macos
xcodegen generate
open PowermonMenuBar.xcodeproj      # or build from the CLI:
xcodebuild -project PowermonMenuBar.xcodeproj -scheme Powermon -configuration Debug build
xcodebuild -project PowermonMenuBar.xcodeproj -scheme Powermon test
```

The built app lands in DerivedData; `Powermon.app` can be copied to
`/Applications`.

## First run

The app starts with no configuration. Open **Settings** (⌘,) from the panel and set:

- **Server URL** — `http://marzmesas-trainbox:8787` over Tailscale, or
  `http://localhost:8787` through an SSH tunnel.
- **Token** — the `server.token` from the server's `config.toml`. Stored in the
  Keychain. Not needed for a tunnel, because that arrives as loopback.

**Test connection** reports which of the three outcomes you got: connected,
token rejected, or unreachable.

## State of the build

Milestones from the spec, §13:

| | |
|---|---|
| M1 talks to the server | done — 15 tests over a live payload and six hand-edited variants |
| M2 menu bar number | done |
| M3 popover | done — hero, meters, totals, processes |
| M4 settings + Keychain | done |
| M5 robustness | done — every §5.3 state covered by `PollerTests`, plus unreachable and 401 exercised against the real server |
| M6 polish | app icon done; light/dark verified by rendered snapshots. Launch at login is implemented but cannot be verified without a signing identity |

## Tests

`xcodebuild -project PowermonMenuBar.xcodeproj -scheme Powermon test` — 24 tests,
about 20 s (the poller tests wait on real timers).

- `SnapshotDecodingTests` — the live payload and the six §15 variants.
- `PowermonClientTests` — 401 mapped before decoding, token sent as a header and
  never in the URL.
- `PollerTests` — the §5.3 state machine: live, 401, stale after one failure,
  unreachable after a sustained outage, wedged sampler, recovery without a
  relaunch, and immediate retry on a settings change.
- `PopoverRenderTests` — renders the panel for five fixtures in both appearances,
  asserts it is not blank, and writes PNGs to a temp directory (the path is
  printed) so the layout can be eyeballed without launching anything.

The app icon is generated, not hand-drawn: `swift Tools/make-icon.swift`.

### Known deviations from the spec

- **`ObservableObject`, not `@Observable`.** §7 asks for `@Observable`, which is
  macOS 14+, while the stated target is macOS 13. The deployment target won.
- **Palette in code, not an asset catalog.** §6 prefers an asset catalog; a
  dynamic `NSColor` provider (`Views/Palette.swift`) resolves per appearance the
  same way without ~30 JSON files, and still keeps `colorScheme` out of view code.
- **Settings are a screen inside the panel, not a window.** §5.4 asks for the
  standard `Settings` scene. That scene does not work in this app at all:
  `showSettingsWindow:` reports the action as handled and no window is ever
  created, with the app as either `.accessory` or `.regular`. A hand-rolled
  `NSWindow` did work, but opened on top of the panel it was launched from.
  `Views/SettingsPanelView.swift` is now a second screen in the same 300 pt
  panel, reached by the Settings button and left by Back or Escape.
  Consequence: ⌘, only works while the panel is open, not globally.
- **App Sandbox is off.** §10 asks for the sandbox with
  `com.apple.security.network.client`. There is no Developer ID on this machine,
  and a sandboxed app with no team ID cannot reach the Keychain
  (`errSecMissingEntitlement`, -34018). Turn both on together once there is a team.
- **The model decodes only rendered fields.** `disk`, `net`, `session`,
  `totals.all` and the non-`total` sparklines are ignored; `Codable` skips them.
  Adding one back is a single line.
- **`/healthz` is not polled.** "Up but not sampling" is detected by noticing the
  server's own `ts` stop advancing, which is the same signal without a second
  request per tick and without comparing clocks.

### The snake_case trap

`.convertFromSnakeCase` maps `projected_30d_kwh` to **`projected30DKwh`** —
capital D, because `"30d".capitalized` is `"30D"`. `cost_per_h`, `clock_mhz`,
`mem_mib` and `last_sample_age_s` all convert the obvious way. There is a test
pinning this.
