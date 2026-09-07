# Changelog

Notable changes per release. Dates are the release date; the project follows
[semantic versioning](https://semver.org), with 0.x meaning the JSON API may
still change shape between minor versions.

## 0.1.0 — 2026-09-07

First versioned release. Everything before this was unversioned development.

### Security

- **An empty `server.token` no longer authorises every client.** The check
  returned success before comparing anything, so binding to `0.0.0.0` without a
  token served all data to anyone who could reach the port — the opposite of
  what the config comment promised. An unset token now denies remote requests,
  and that combination refuses to start at all, exiting before the socket opens.
- **A reverse proxy on the same host no longer disables authentication.**
  Proxied requests arrive from `127.0.0.1` and so satisfied the loopback
  exemption, meaning following the README's own TLS advice removed
  authentication entirely. Forwarding headers are now believed only from
  addresses in `server.trusted_proxies`.
- Named API keys in `server.keys_file`, stored hashed and revocable one at a
  time, via `--add-key` and `--revoke-key`. `server.token` still works as a
  one-key shorthand.
- Optional TLS via `server.tls_cert` and `server.tls_key`, validated at startup.
- Repeated wrong keys from one address are slowed. A correct key is never
  delayed, so one bad client cannot lock out the rest.
- `?token=` still works but warns: it reaches browser history and proxy logs.

### Correctness

- **A failed sensor read is no longer recorded as zero consumption.** A driver
  hiccup used to lower recorded energy and cost silently. Incomplete intervals
  now contribute no energy and are counted as missing time, with `coverage`
  reported per period.
- **Every GPU and every CPU package is read**, not just the first. A multi-GPU
  or multi-socket host previously reported one device's draw as the whole
  machine's.
- **Editing the tariff no longer rewrites history.** Standing charges are
  recorded per day as they are sampled; all three periods now apply the same
  rule, so "all time" means all electricity.
- Period windows are half-open and normalised to local midnight, so a
  daylight-saving day ends where the calendar says.

### Added

- **Wall meters.** `[meter]` reads a real meter over HTTP (Tasmota, Shelly,
  Home Assistant) or NUT (UPS), making the headline number a measurement rather
  than a model, and reporting `residual_w` so `baseline_w` and
  `psu_efficiency` can be calibrated.
- A `health` block on `/api/now` and `/healthz`: sampler state, meter and
  sensor availability, coverage, and the live CPU/GPU sources.
- `gpu.devices[]` for per-card detail alongside the host aggregate.
- A macOS menu bar client, in `macos/`.
- `pwr` reads `POWERMON_TOKEN`, so it can reach a remote instance, and
  distinguishes a rejected token from an unreachable server.
- MIT licence and CI running the Python and macOS suites.

### Changed

- **GPU telemetry comes from NVML rather than forking `nvidia-smi`**, cutting
  daemon CPU from 2.35 % of a core to 0.25 % (47 ms per sample to 5 ms) on the
  reference box. `nvidia-smi` remains the fallback.
- The published overhead figure was corrected: it had claimed 0.2 % of a core
  and 22 MB, against a measured 2.55 % and 35 MB, before the NVML change fixed
  the underlying cost.
- The dashboard reports failed requests instead of silently holding stale
  numbers, and shows health, coverage and per-GPU rows.
