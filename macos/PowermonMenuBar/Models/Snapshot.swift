import Foundation

/// Mirror of `GET /api/now`. Decoded with `.convertFromSnakeCase`.
///
/// Only the fields the UI renders are declared — `Codable` ignores the rest of
/// the payload (disk, net, session, totals.all, the non-`total` sparklines).
/// Adding one back is a single line; see the design doc §4 for the full schema.
///
/// Nullability follows the server exactly: a field is optional here only where
/// the server can send `null`. Those go null when hardware is absent or a sensor
/// read fails, and the UI must render them as "—", never as 0.
struct Snapshot: Codable, Sendable, Equatable {
    let ts: Double
    let power: Power
    let cpu: CPU
    let gpu: GPU
    let mem: Memory
    let busy: Bool
    let totals: Totals
    let spark: Spark
    let meta: Meta

    struct Power: Codable, Sendable, Equatable {
        let totalW: Double
        let cpuW: Double
        let gpuW: Double
        let costPerH: Double
        /// "rapl" = measured from the CPU's energy counter, "estimated" = a
        /// utilisation model accurate to about ±20 W.
        let source: String

        var isMeasured: Bool { source == "rapl" }
    }

    struct CPU: Codable, Sendable, Equatable {
        let temp: Double?
        /// Configured package power limit — the CPU meter's denominator.
        let maxW: Double
    }

    struct GPU: Codable, Sendable, Equatable {
        let present: Bool
        let temp: Double?
        let clockMhz: Double?
        let memUsed: Double?
        let memTotal: Double?
        /// Power limit — the GPU meter's denominator.
        let limitW: Double?
        /// True when this sample's GPU read failed. Distinct from "0 watts".
        let error: Bool
        let procs: [Proc]

        struct Proc: Codable, Sendable, Equatable, Identifiable {
            let pid: String
            let name: String
            let memMib: Double?

            var id: String { pid }
        }
    }

    struct Memory: Codable, Sendable, Equatable {
        let totalGib: Double
        let usedGib: Double
    }

    struct Totals: Codable, Sendable, Equatable {
        let today: Period
        let month: Period

        struct Period: Codable, Sendable, Equatable {
            let kwh: Double
            let cost: Double
            /// Present on `month` only.
            ///
            /// Capital D is not a typo: `.convertFromSnakeCase` capitalises each
            /// component, and `"30d".capitalized` is `"30D"`, so
            /// `projected_30d_kwh` becomes `projected30DKwh`.
            let projected30DKwh: Double?
            let projected30DCost: Double?
        }
    }

    struct Spark: Codable, Sendable, Equatable {
        /// ~90 points, oldest to newest, roughly the last 3 minutes.
        let total: [Double]
    }

    struct Meta: Codable, Sendable, Equatable {
        let symbol: String
        let host: String
        let uptimeS: Double
        /// Server sample period in seconds — polling faster than this only
        /// returns duplicates.
        let interval: Double
    }
}

/// Mirror of `GET /healthz`. `ok` is false when the HTTP server is alive but
/// the sampler has not produced a sample in over 30 s.
struct Health: Codable, Sendable, Equatable {
    let ok: Bool
    let lastSampleAgeS: Double
}
