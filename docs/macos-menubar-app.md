# powermon for macOS — menu bar app design doc

**Status:** ready to build · **Target:** macOS 13 Ventura or later · **Language:** Swift 5.9+ / SwiftUI
**Audience:** the agent or developer implementing this on a Mac.

---

## 1. What this is

A menu bar app that shows, at a glance, what a remote training server is drawing
right now and what it is costing. Clicking the menu bar item opens a small panel
with live detail. It is a **client** of the existing `powermon` service — it
collects nothing itself.

The number lives in the menu bar because it is something you *glance at*, not
something you *visit*. That framing drives every decision below.

### Non-goals

Deliberately out of scope. Resist scope creep toward them; the web dashboard
already does these well and duplicating it in a popover makes both worse.

- Historical charts (24 h / 7 d / 30 d). A "Open dashboard" button opens the web UI.
- Configuring the server (tariff, baseline, calibration). That is `config.toml`.
- Controlling the machine — no restart, shutdown, or process killing.
- Multi-server monitoring. One server. If you need several, that is a different app.
- Notifications/alerts in v1. See §14 for why this is deferred, not rejected.

---

## 2. Decision record: HTTP, not SSH

The original idea was to SSH into the server and run a command. **Do not do
this.** It was considered and rejected for concrete reasons:

| SSH approach | HTTP approach |
|---|---|
| Bundle libssh2 or shell out to `ssh` | `URLSession`, in the standard library |
| App Sandbox makes spawning `ssh` painful; blocks Mac App Store distribution | Sandbox-friendly, one entitlement |
| Manage keys, passphrases, `known_hosts`, Keychain plumbing | One bearer token in Keychain |
| Parse CLI text back into numbers | `Codable` structs from typed JSON |
| TCP + auth handshake per poll, or babysit a long-lived connection through laptop sleep | Stateless request, ~9 KB, sub-millisecond over Tailscale |

The server already exposes exactly the JSON this app needs. Measured on the
target tailnet: **9,127 bytes per `/api/now` response, 0.86 ms round trip.**

SSH would be the right call only if there were no daemon to talk to. There is one.

---

## 3. The backend

`powermon` runs on the Linux training box as a systemd user service, samples
every 2 s, and serves HTTP on port 8787. Repo root has the full README.

### Reaching it

**Prerequisite:** the server ships bound to `127.0.0.1` with an empty
`server.token`, which routes 1 and 2 below cannot reach. Before this app is
useful over the network, `config.toml` needs `server.host` widened (`0.0.0.0`, or
the Tailscale IP to stay off the LAN) **and** a token set. Route 3 works against
the default config unchanged — worth knowing when a fresh box appears
unreachable for reasons that have nothing to do with the app.

Three routes from a Mac:

1. **Tailscale (expected)** — `http://<server>:8787`, MagicDNS name or `100.x.y.z`.
   Works from anywhere both devices are on the tailnet. **Token required.**
2. **Same LAN** — `http://192.168.x.y:8787`. **Token required.**
3. **SSH tunnel** — `ssh -L 8787:127.0.0.1:8787 user@server`, then
   `http://localhost:8787`. Arrives as loopback, so **no token needed**.

### Auth model

- Requests from `127.0.0.1` / `::1` are **always allowed, no token**.
- Every other client must present the token from `server.token` in `config.toml`.
- Three accepted channels — the app should use the **header**:
  - `X-Powermon-Token: <token>` ← use this
  - `?token=<token>` query parameter
  - `powermon_token` cookie
- Wrong or missing token on an `/api/*` path → **HTTP 401**, body
  `{"error": "unauthorised: token required"}`.

The token is a shared secret over **plain HTTP**. Acceptable on a tailnet;
it is not TLS. See §11 for what this forces in `Info.plist`.

---

## 4. API contract

Ground truth, captured from a live server. Treat this as the schema.

### `GET /api/now` → 200

The only endpoint the app needs for normal operation. Every numeric field is a
JSON number (decode as `Double` unless noted). **Fields marked nullable can be
`null` and the app must handle it** — they go null when hardware is absent or a
sensor read fails.

```jsonc
{
  "ts": 1786210661.79,              // Unix seconds, server clock, when sampled

  "power": {
    "total_w":     104.88,          // wall power — THE headline number
    "cpu_w":        30.11,
    "gpu_w":        29.28,
    "other_w":      45.48,          // board+RAM+drives+PSU loss (derived)
    "cost_per_h":    0.0157,        // in meta.currency
    "source": "estimated"           // "rapl" = measured | "estimated" = modelled
  },

  "cpu": {
    "pct": 0.41,                    // 0–100
    "temp": 35.75,                  // nullable, °C
    "freq_mhz": 2485.53,            // nullable
    "load": [0.12, 0.09, 0.03],     // 1/5/15 min
    "cores": 24,                    // Int
    "max_w": 142.0                  // configured PPT, use as meter denominator
  },

  "gpu": {
    "present": true,                // false ⇒ hide every GPU element
    "name": "NVIDIA GeForce RTX 3090",  // nullable
    "util": 0.0,                    // nullable, 0–100
    "temp": 42.0,                   // nullable, °C
    "fan": 31.0,                    // nullable, %
    "clock_mhz": 210.0,             // nullable
    "mem_used": 23102.0,            // nullable, MiB
    "mem_total": 24576.0,           // nullable, MiB
    "limit_w": 350.0,               // power limit, meter denominator
    "error": false,                 // true ⇒ this sample's GPU read failed
    "procs": [                      // may be empty
      { "pid": "3478", "name": "VLLM::EngineCore", "mem_mib": 21830.0 }
    ]
  },

  "mem":  { "total_gib": 31.26, "used_gib": 5.73, "pct": 18.33 },
  "disk": { "total_gib": 915.32, "used_gib": 374.08, "pct": 40.86 },
  "net":  { "rx_mbps": 0.008, "tx_mbps": 0.007 },

  "busy": false,                    // server's own training-vs-idle verdict

  "totals": {
    "today": { "kwh": 2.09, "busy_kwh": 0.0, "idle_kwh": 2.09,
               "cost": 0.31, "peak_w": 167.95, "hours": 19.62 },
    "month": { "kwh": 19.67, "busy_kwh": 0.37, "idle_kwh": 19.29,
               "cost": 2.95, "peak_w": 458.71, "hours": 185.12,
               "projected_30d_kwh": 76.50, "projected_30d_cost": 11.47 },
    "all":   { "kwh": 21.11, "busy_kwh": 0.38, "idle_kwh": 20.72,
               "cost": 3.16, "peak_w": 458.71, "hours": 199.30 }
  },

  "session": { "wh": 9649.38, "cost": 1.44, "seconds": 329766.57 },

  "spark": {                        // ~90 points, oldest→newest, ≈3 min at 2 s
    "ts":       [/* Double */],
    "total":    [/* Double */],
    "gpu":      [/* Double */],
    "cpu":      [/* Double */],
    "gpu_temp": [/* Double? */],
    "cpu_temp": [/* Double? */]
  },

  "meta": {
    "symbol": "€", "currency": "EUR", "rate": 0.15, "mode": "flat",
    "baseline_w": 35.0, "psu_efficiency": 0.9,
    "interval": 2.0,                // server sample period, seconds
    "host": "marzmesas-trainbox",
    "uptime_s": 329770.41,
    "standing_charge_per_day": 0.0
  }
}
```

**Semantics worth respecting:**

- `power.source` — when `"estimated"`, CPU watts come from a utilisation model
  (±20 W), not a sensor. **Surface this.** Do not present a modelled number with
  the same confidence as a measured one.
- `totals.*.hours` is *recorded* time, not wall-clock elapsed. Downtime is
  excluded by design.
- `projected_30d_*` = average draw over recorded hours × 30 days. It is not
  "elapsed calendar days" scaled.
- `session.*` resets when the service restarts. Of limited interest — the app can ignore it.

### `GET /api/history?range=24h|7d|30d|90d` → 200

Not needed for v1. Documented so nobody re-derives it: returns
`{ "range": "24h", "series": { "t": [...], "total": [...], "cpu": [...],
"gpu": [...], "other": [...], "gpu_temp": [...], "cpu_temp": [...],
"busy_frac": [...], "kwh": [...], "bucket_s": 300 } }`. `24h` is 5-minute
averages from raw samples; longer ranges are hourly aggregates and carry
`cost` but have `null` temperatures.

### `GET /healthz` → 200

`{"ok": true, "last_sample_age_s": 1.31}` — `ok` is false when the last sample is
over 30 s old, i.e. the HTTP server is alive but the sampler is wedged. **Also
token-gated.** Useful for distinguishing "server unreachable" from "server up but
not sampling".

### Errors

| Condition | Status | Body |
|---|---|---|
| Missing/invalid token on `/api/*` | 401 | `{"error": "unauthorised: token required"}` |
| Unknown path | 404 | `{"error": "not found"}` |
| Server-side exception | 500 | `{"error": "<repr>"}` |

There is no rate limiting. Polling every 2 s is fine — it is what the web
dashboard does.

---

## 5. UX specification

### 5.1 Menu bar title

The contested real estate. **One value, never a dashboard.**

Default format: `● 287 W`

- **Status dot** — 8 pt filled circle:
  - training (`busy == true`) → series blue
  - idle → secondary text colour
  - stale/unreachable → status critical
- **Value** — configurable in Settings, default watts:
  - `287 W` — `power.total_w`, rounded to integer
  - `€0.043/h` — `power.cost_per_h`, 3 decimals
  - `287 W · €0.04/h` — both, for wide menu bars
- **Font** — `.system(size: 13).monospacedDigit()`. **Monospaced digits are
  mandatory**: without them the item changes width on every update and shoves
  neighbouring menu bar items around.
- Never show more than ~10 characters.
- When unreachable: `● —` (dot critical, em dash). **Never a stale number.**

### 5.2 Popover

`MenuBarExtra` with `.menuBarExtraStyle(.window)`. Fixed width **300 pt**, height
grows with content (~380–440 pt typical).

```
┌──────────────────────────────────┐
│ marzmesas-trainbox      ● training│   host + state
│ up 3d 20h · CPU estimated         │   secondary, 11pt
├──────────────────────────────────┤
│                                   │
│   287 W          €0.043 /h        │   hero row, 34pt semibold
│   ▁▂▃▅▇▇▆▅▃▂▁▁▂▃▅▇  (sparkline)   │   48pt tall, spark.total
│                                   │
├──────────────────────────────────┤
│ GPU     221 W  ███████░░░  78 °C  │   meters
│ CPU      46 W  ██░░░░░░░░  61 °C  │
│ VRAM   22.6/24 GiB ████████░      │
│ RAM     5.7/31 GiB ██░░░░░░░      │
├──────────────────────────────────┤
│ Today        2.09 kWh      €0.31  │   totals
│ Month       19.67 kWh      €2.95  │
│ Projected   76.51 kWh     €11.48  │
├──────────────────────────────────┤
│ On the GPU                        │   only if procs non-empty
│   VLLM::EngineCore      21.3 GiB  │
├──────────────────────────────────┤
│ Open dashboard    Settings   Quit │   footer buttons
└──────────────────────────────────┘
```

**Field mapping** — no invented numbers:

| Row | Source |
|---|---|
| Host / uptime | `meta.host`, `meta.uptime_s` |
| State chip | `busy` → "training" / "idle" |
| CPU source note | `power.source == "rapl" ? "CPU measured" : "CPU estimated"` |
| Hero watts | `power.total_w` |
| Hero cost | `power.cost_per_h` + `meta.symbol` |
| Sparkline | `spark.total` |
| GPU meter | `power.gpu_w` / `gpu.limit_w`, label `gpu.temp` |
| CPU meter | `power.cpu_w` / `cpu.max_w`, label `cpu.temp` |
| VRAM meter | `gpu.mem_used` / `gpu.mem_total` |
| RAM meter | `mem.used_gib` / `mem.total_gib` |
| Today / Month | `totals.today`, `totals.month` (`kwh`, `cost`) |
| Projected | `totals.month.projected_30d_*` |
| Processes | `gpu.procs` (hide section when empty) |

**Conditional rendering:** when `gpu.present == false`, hide the GPU meter, VRAM
meter and process section entirely — do not render empty rows. When
`gpu.error == true` for the current sample, keep the rows but show `—` for GPU
values rather than zeros. **Zero and unknown are different states**; a 0 W GPU
reading is a lie when the truth is "the read failed".

### 5.3 States

| State | Menu bar | Popover |
|---|---|---|
| Launching, no data yet | `● …` | "Connecting…" |
| Live | `● 287 W` | full content |
| Stale (no successful poll in 3× interval) | last value, dot dimmed | banner "Last updated 14 s ago", content held at 55 % opacity |
| Unreachable (network error / timeout) | `● —` | "Can't reach <host>" + Retry + Settings |
| 401 | `● —` | "Token rejected" + Open Settings |
| Sampler wedged (`healthz.ok == false`) | `● 287 W` with warning dot | banner "Server is up but not sampling" |

**Hold the previous render rather than flashing a skeleton.** Dim, don't blank.

### 5.4 Settings window

Standard `Settings` scene, ⌘, — one pane:

- **Server URL** — text field, e.g. `http://marzmesas-trainbox:8787`. Validate as URL.
- **Token** — `SecureField`. Stored in Keychain, never `UserDefaults`.
- **Test connection** — button; performs one `GET /api/now` and reports
  ✓ connected / ✗ 401 token rejected / ✗ unreachable, with the underlying error.
- **Menu bar shows** — picker: Watts / Cost per hour / Both.
- **Refresh interval** — picker: 2 s / 5 s / 10 s (default **5 s**).
- **Launch at login** — toggle, `SMAppService.mainApp`.

---

## 6. Visual design

Reuse the web dashboard's palette so the two read as one product. These are
validated for colour-blind separation and contrast in **both** modes — use them
as-is rather than picking new colours.

| Role | Light | Dark |
|---|---|---|
| GPU series | `#2a78d6` | `#3987e5` |
| CPU series | `#eb6834` | `#d95926` |
| Other/rest | `#1baf7a` | `#199e70` |
| Meter track | `#cde2fb` | `#184f95` |
| Primary text | `#0b0b0b` | `#ffffff` |
| Secondary text | `#52514e` | `#c3c2b7` |
| Muted | `#898781` | `#898781` |
| Status good | `#0ca30c` | `#0ca30c` |
| Status warning | `#fab219` | `#fab219` |
| Status critical | `#d03b3b` | `#d03b3b` |

Prefer an asset catalog with light/dark variants so SwiftUI resolves them
automatically; do not branch on `colorScheme` in view code.

**Rules carried over from the dashboard:**

- **Meter fill** = accent, going warning ≥ 75 %, critical ≥ 90 %. Unfilled track is
  a lighter step of the same hue, never grey.
- **Sparkline**: 2 pt line, series blue, area fill at ~12 % opacity, 4 pt end dot.
  No axes, no gridlines, no labels — it shows shape, the hero number shows value.
- **Text never wears the series colour.** Identity comes from a coloured mark
  beside the text.
- Values use **proportional** figures at hero size; `monospacedDigit()` only in
  the menu bar title and aligned columns (the totals rows).
- Hero number ≥ 30 pt, system font. Exactly one hero.

---

## 7. Architecture

```
PowermonMenuBar/
├── PowermonMenuBarApp.swift    @main, MenuBarExtra scene, Settings scene
├── Models/
│   ├── Snapshot.swift          Codable mirror of /api/now
│   └── ConnectionState.swift   enum: connecting/live/stale/unreachable/unauthorized
├── Services/
│   ├── PowermonClient.swift    URLSession wrapper, one fetch method
│   ├── Poller.swift            @Observable, owns the timer + current state
│   ├── KeychainStore.swift     token read/write
│   └── Settings.swift          @AppStorage-backed prefs (NOT the token)
├── Views/
│   ├── MenuBarLabel.swift      the title: dot + number
│   ├── PopoverView.swift       composition of the sections below
│   ├── HeroView.swift          watts + cost + sparkline
│   ├── MeterRow.swift          reusable labelled meter
│   ├── TotalsView.swift        today/month/projected
│   ├── ProcessListView.swift   gpu.procs
│   ├── Sparkline.swift         Path-based, or Swift Charts
│   └── StatusBanner.swift      stale / unreachable / wedged messaging
└── Resources/
    ├── Assets.xcassets         colour sets, app icon
    └── Info.plist              LSUIElement, ATS exception
```

**Data flow:** `Poller` holds a `Timer`, calls `PowermonClient.fetch()`, and
publishes `(snapshot, state, lastSuccess)`. Views observe `Poller`. There is no
other state. Keep the snapshot immutable and replace it wholesale — no partial
mutation.

**Concurrency:** `PowermonClient.fetch()` is `async throws`. `Poller` is
`@MainActor @Observable`. Do decoding off the main actor if profiling says so;
at 9 KB it will not.

**`LSUIElement = true`** in Info.plist — no Dock icon, no main window.

---

## 8. Key type signatures

Sketches, not gospel — but the shapes matter.

```swift
struct Snapshot: Codable, Sendable {
    let ts: Double
    let power: Power
    let cpu: CPU
    let gpu: GPU
    let mem: Usage
    let disk: Usage
    let busy: Bool
    let totals: Totals
    let spark: Spark
    let meta: Meta

    struct Power: Codable, Sendable {
        let totalW, cpuW, gpuW, otherW, costPerH: Double
        let source: String          // "rapl" | "estimated"
        var isMeasured: Bool { source == "rapl" }
    }
    struct GPU: Codable, Sendable {
        let present: Bool
        let name: String?
        let util, temp, fan, clockMhz, memUsed, memTotal: Double?
        let limitW: Double?
        let error: Bool
        let procs: [Proc]
        struct Proc: Codable, Sendable, Identifiable {
            let pid: String, name: String, memMib: Double?
            var id: String { pid }
        }
    }
    // ... CPU, Usage, Totals, Spark, Meta likewise
}
```

Use `JSONDecoder.keyDecodingStrategy = .convertFromSnakeCase` — the API is
snake_case throughout, so `total_w` → `totalW` maps cleanly. **Verify
`cost_per_h` → `costPerH` and `clock_mhz` → `clockMhz` decode correctly**; the
strategy handles them, but assert it in a test rather than assuming.

```swift
enum ConnectionState: Equatable {
    case connecting
    case live
    case stale(since: Date)
    case unreachable(String)     // user-facing reason
    case unauthorized
    case samplerStalled          // healthz.ok == false
}

final class PowermonClient {
    init(baseURL: URL, token: String?)
    func fetchNow() async throws -> Snapshot
    func fetchHealth() async throws -> Health
}
```

`URLRequest`: set `timeoutInterval = 5`, `cachePolicy = .reloadIgnoringLocalCacheData`,
and `X-Powermon-Token` when a token exists. Map `401` to
`ConnectionState.unauthorized` **before** attempting to decode — the 401 body is
not a `Snapshot`.

---

## 9. Polling and battery

- Default **5 s** when the popover is closed; **2 s** while it is open. Server
  samples every 2 s (`meta.interval`), so faster than that returns duplicates.
- **Pause polling** when the display sleeps or the machine suspends. Observe
  `NSWorkspace.shared.notificationCenter` for `willSleepNotification` /
  `didWakeNotification`. On wake, fetch immediately rather than waiting for the
  next tick.
- Backoff on failure: 5 s → 10 s → 30 s → 60 s, reset on first success. A
  server that is off should not mean a request every 5 s forever.
- Never block the main thread on a request. The menu bar must stay responsive
  while the server is unreachable.

---

## 10. Security

- **Token in Keychain**, via `KeychainStore`. Service `com.<you>.powermon`,
  account = the server host. Never `UserDefaults`, never in a plist, never logged.
- Sandbox entitlement: `com.apple.security.network.client` = YES.
- Do not log the token, and redact it from any error surfaced to the UI.

---

## 11. The ATS gotcha — read this before debugging a mystery failure

The server is **plain HTTP**. macOS App Transport Security blocks cleartext HTTP
by default, and a Tailscale `100.x` address does **not** qualify for
`NSAllowsLocalNetworking`. Without an exception, every request fails with
`NSURLErrorAppTransportSecurityRequiresSecureConnection (-1022)` and it will look
like a network bug.

Add to `Info.plist`:

```xml
<key>NSAppTransportSecurity</key>
<dict>
  <key>NSAllowsArbitraryLoads</key>
  <true/>
</dict>
```

`NSAllowsArbitraryLoads` is broad. It is justified here because the server URL is
user-supplied and can be a tailnet IP, a MagicDNS name, a LAN IP, or localhost —
which cannot be enumerated as exception domains ahead of time. If the app is ever
distributed on the Mac App Store, expect to justify this in review; the honest
answer is "the user points it at their own machine on a private network."

---

## 12. Edge cases

| Case | Required behaviour |
|---|---|
| Server unreachable | `● —`, never a stale number in the menu bar |
| 401 | Distinct "token rejected" message, not "unreachable" — different fix |
| `gpu.present == false` | Hide all GPU UI; app still useful for CPU-only boxes |
| `gpu.error == true` | Show `—` for GPU values, not `0` |
| Nullable temps are `null` | Show `—`; never render `0 °C` |
| `power.source == "estimated"` | Label CPU as estimated in the popover |
| `healthz.ok == false` | Warning banner: up but not sampling |
| Server clock skew | Compute staleness from **local** elapsed time since last successful fetch, not by comparing to `ts` |
| `totals.month.kwh == 0` | Guard division when deriving idle share |
| Very long `meta.host` | Truncate with tail ellipsis in the header |
| Popover open while unreachable | Keep last content dimmed + banner; do not blank |
| Wake from sleep | Immediate fetch; show `connecting`, not a stale value |

---

## 13. Milestones

Each has a testable acceptance criterion. Ship them in order.

**M1 — Talks to the server.** CLI-ish: a Swift test or scratch target that
fetches `/api/now` and prints `total_w`.
*Done when:* decoding succeeds against the real server **and** a saved sample
payload, with a unit test asserting `costPerH` and `clockMhz` decode.

**M2 — Menu bar shows a live number.** `MenuBarExtra`, title only, hardcoded URL/token.
*Done when:* the menu bar shows watts updating on an interval, with monospaced
digits and no width jitter.

**M3 — Popover.** Hero, meters, totals, processes.
*Done when:* every field in the §5.2 mapping renders from live data, GPU sections
hide when `present == false`, and nulls render `—`.

**M4 — Settings + Keychain.** URL, token, interval, menu bar format, test button.
*Done when:* the app launches with no config, is pointed at a server through the
UI, and the token survives a relaunch.

**M5 — Robustness.** All §12 states, backoff, sleep/wake.
*Done when:* pulling the network shows `● —` within one interval, restoring it
recovers without a relaunch, and a wrong token shows the 401 message.

**M6 — Polish.** App icon, launch at login, "Open dashboard", light/dark check.
*Done when:* it looks right in both appearances and starts on login.

---

## 14. Deferred

- **Alerts** — "notify if idle above X W for Y minutes" is the obvious v2, and
  the real payoff (you would learn about a forgotten idle GPU without looking).
  Deferred because it needs threshold state, notification permission, and a
  do-not-spam policy — a feature, not a detail. `busy` and `totals.*.idle_kwh`
  are the inputs.
- **History charts** — the web dashboard does this; `/api/history` is documented
  in §4 if the panel ever wants a 24 h strip.
- **Multiple servers** — would change the menu bar title model entirely.

---

## 15. Testing without a server

Save a real payload as a fixture so the UI can be built offline:

```sh
curl -s -H "X-Powermon-Token: $TOKEN" http://<server>:8787/api/now > sample-now.json
```

Then hand-edit variants and add each as a test fixture:

- `gpu.present = false` — CPU-only machine
- `gpu.error = true`, GPU numbers null — driver hiccup
- `power.source = "rapl"` — measured CPU path
- all temps null — no sensors
- `gpu.procs = []` — nothing on the card
- `totals.month.kwh = 0` — fresh install, division guards

A snapshot test per fixture catches the "renders 0 instead of —" class of bug,
which is the most likely regression in this app.

---

## 16. Quick reference

```sh
# explore the API by hand
export PM=http://<server>:8787
export TOKEN=<server.token from config.toml>

curl -s -H "X-Powermon-Token: $TOKEN" $PM/api/now | jq .power
curl -s -H "X-Powermon-Token: $TOKEN" $PM/healthz
curl -s -H "X-Powermon-Token: $TOKEN" "$PM/api/history?range=24h" | jq '.series | keys'

# expect 401
curl -s $PM/api/now
```

Server-side operations, if the API is not responding:

```sh
systemctl --user status powermon
journalctl --user -u powermon -f
```
