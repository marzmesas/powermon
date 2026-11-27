# Changelog

Notable changes per release. Dates are the release date; the project follows
[semantic versioning](https://semver.org), with 0.x meaning the JSON API may
still change shape between minor versions.

## Unreleased

- **Price from a contract.** The Settings page can read the price out of a
  contract PDF. The text is extracted and redacted on the machine
  (`POST /api/contract/text`, using `pdftotext` or optional `pypdf`). The
  person reviews it, and only that text goes to an OpenAI-compatible model
  configured in a new file-only `[llm]` section
  (`POST /api/contract/analyse`). powermon converts the model's raw figures
  itself, keeps only figures whose quotes appear in the text, and fills in
  the form without saving. `GET /api/settings` gains `contract` (admins only).
  The Docker image now includes poppler-utils.
- **macOS popover restyled to match the dashboard.** Same palette in light and
  dark, a state chip, a watts dial, the same one-sentence status line with a
  one-line cost summary under it (today, month, projected, idle this month),
  and a collapsible "where it goes" card that starts folded but still shows a
  one-line summary and any warning.
  The menu bar icon stays one colour.
- **`pwr` restyled to match.** A mascot, a state chip, the same spoken
  sentence, GPU/CPU/REST bars on one shared watt scale, readouts and a one-line
  cost summary, in the dashboard's colours (24-bit when `COLORTERM` allows,
  else 256-colour). New `--no-color` and `--color`; `NO_COLOR` and piping give
  plain text.

## 0.2.0 (not yet tagged)

### Cost and price

- **Time-of-day and dynamic tariffs.** `tariff.mode` gains `tou` (fixed prices
  by hour range, with a weekend schedule and holidays) and `dynamic` (a market
  price from energy-charts.info for European zones, Spain's PVPC, or any JSON
  endpoint), next to `flat`. `tariff.tax_pct` adds taxes to every price.
- **Cost per training run.** Each busy stretch is recorded as a run with its
  energy, cost, peak and average draw and the process on the card
  (`activity.run_min_s`, `activity.run_gap_s`). New `/api/runs` (JSON or
  `?format=csv`), `run` in `/api/now`, a "Training runs" card on the dashboard,
  and `pwr run -- <command>` to price one command.
- **Idle cost.** Totals carry `idle_cost`, and the dashboard leads with what
  the month cost while the machine was on and not working.
- **CO2 beside cost.** `[carbon] g_per_kwh` adds CO2 to totals, runs, the live
  reading and `/metrics`. At 0, the default, nothing is reported.
- **Fixed:** the since-start cost used the current price for all energy, which
  was wrong under a time-of-day or dynamic tariff.

### Alerts and power saving

- **Idle-draw alerts.** `[alerts]` pushes to an ntfy topic when the machine
  stays above `idle_w` with the GPU idle, reminds once, and reports when it
  clears. The message names the process using the most CPU.
- **More alerts.** GPU and CPU temperature, disk usage and a monthly budget,
  plus a generic JSON `alerts.webhook_url` beside ntfy. `/api/now` gains
  `alerts[]`, and the dashboard shows active alerts as banners.
- **Sleep when idle (opt-in).** `[suspend] after_idle_min` suspends the machine
  once the GPU is idle, the CPU is quiet, no run is open and nobody is logged
  in. A real suspend and wake have not been tested.

### Setup

- **Installer.** `install.sh` sets up the config, an access token, the systemd
  user service and `pwr` in one command, and prints the URL to connect to.
- **Settings page.** Price, alerts and the sleep timer can be set from the
  dashboard and apply without a restart. Changes are stored in `settings.json`,
  which overrides `config.toml`. New `/api/settings` (GET, POST, DELETE), admin
  scope, limited to a whitelist of keys. A helper works a rate out from a bill.
- **Docker.** A `Dockerfile` and `docker-compose.yml`. The entrypoint writes a
  config on first start with a token from `POWERMON_TOKEN` or a generated one.
  GPU and RAPL passthrough are documented but untested.

### Integrations

- **Exports.** `/api/history?format=csv`, and `GET /metrics` in Prometheus
  text format behind the same token rule as the API.
- **MQTT and Home Assistant (opt-in).** `[mqtt] host` publishes the readings
  with Home Assistant discovery, using a small built-in MQTT 3.1.1 client.
  Checked against a Mosquitto 2 broker, not against a Home Assistant instance.
- **Several machines in one view.** `[peers] hosts` makes one instance poll
  its peers; new `/api/peers` and an "All machines" card on the dashboard.
- **AMD GPUs.** Cards driven by `amdgpu` are read from sysfs next to any NVIDIA
  cards. Tested against a fake sysfs tree only, not on hardware.

### Dashboard

- A 90-day range, meter bars that line up, and no overlapping chart labels.
- `dev.py` serves the local `dashboard.html` against a running powermon and
  reloads on save.

### Menu bar app

- A mascot for an icon, shown in the menu bar and tinted by state.
- A macOS notification when the server raises its idle alert and when it
  clears, with a setting to turn it off.
- A Wake button (Wake-on-LAN, same local network only) while the server is
  unreachable. A real wake has not been tested.
- The popover shows idle cost for the month, the run in progress and every
  active alert.
- The Keychain is read once per launch, not once per poll.

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
