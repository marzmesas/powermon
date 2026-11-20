<p align="center">
  <img src="macos/PowermonMenuBar/Resources/Assets.xcassets/AppIcon.appiconset/icon_128x128@2x.png" width="128" alt="powermon icon">
</p>

<h1 align="center">powermon</h1>

<p align="center">
  <b>What your GPU box is drawing right now, and what it is costing you.</b><br>
  A tiny power, resource and cost monitor for Linux training machines.
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#electricity-price">Electricity price</a> ·
  <a href="#alerts">Alerts</a> ·
  <a href="#remote-access">Remote access</a> ·
  <a href="#how-accurate-is-it">Accuracy</a> ·
  <a href="#api">API</a>
</p>

<p align="center">
  <img src="docs/dashboard.png" width="820" alt="The powermon dashboard during a training run (sample data)">
</p>

## What you get

- **Watts at the wall, live**, split into GPU, CPU and everything else.
- **Real money**: cost per hour, today, this month and projected, at a flat,
  time-of-day or live market price.
- **Idle share**: what you paid to leave the machine on while it was not
  training.
- **A push to your phone** when the machine burns power with the GPU idle.
- **Three ways to look**: a web dashboard, `pwr` in the terminal, and a macOS
  menu bar app.
- **Nothing to install**: one Python file, standard library only. It uses
  0.25 % of one core and about 35 MB of RAM, and runs at low priority so it
  never competes with a training job.

## Quick start

On the Linux machine you want to measure (Python 3.10+, systemd):

```sh
git clone https://github.com/marzmesas/powermon.git ~/powermon
~/powermon/install.sh
```

The installer creates `config.toml` with a fresh access token, starts powermon
as a systemd user service that survives logout and reboot, links `pwr` into
`~/.local/bin`, and prints the URL and token to use from other devices.

| Flag | Effect |
|---|---|
| `--local` | listen on `127.0.0.1` only, no token |
| `--rapl` | also install the udev rule for [measured CPU power](#measured-cpu-power) (needs sudo) |

Running it again never touches an existing `config.toml`.

Then:

1. **Open the dashboard** at <http://localhost:8787>, or at the URL the
   installer printed from another device.
2. **Set your [electricity price](#electricity-price)** in `config.toml`.
3. **Optionally** turn on [alerts](#alerts) and install the
   [menu bar app](macos/README.md).

It works without a GPU (the GPU panels hide themselves) and without readable
CPU energy counters (CPU power is then estimated).

## Ways to look at it

| Client | What it is |
|---|---|
| **Web dashboard** | served by powermon itself; the full view, including history |
| **`pwr`** | terminal readout: `pwr` (snapshot), `pwr -w` (live), `pwr --json`. For a remote instance set `POWERMON_TOKEN` or pass `--token` |
| **macOS menu bar app** | wall watts in the menu bar, and a panel with meters, totals and GPU processes. See [`macos/`](macos/README.md) |

The dashboard shows:

- **Hero** — watts at the wall right now, cost per hour, cost if it ran all month.
- **Meters** — GPU power against its 350 W limit, GPU load / memory, CPU power,
  RAM, disk.
- **Tiles** — today, month to date, projected month, **idle share** (what you
  paid to leave it powered on and not training), all time.
- **Charts** — power draw stacked by component, kWh + cost per hour/day,
  temperatures. 24 h / 7 d / 30 d. Every chart has a table view and a tooltip;
  arrow keys move the crosshair.
- **GPU processes** — what's actually on the card.

## Configuration

Everything lives in `config.toml`; [`config.example.toml`](config.example.toml)
documents every key. Restart after editing:

```sh
systemctl --user restart powermon
```

| Section | What it controls |
|---|---|
| `[tariff]` | what a kWh costs: see [electricity price](#electricity-price) |
| `[alerts]` | the idle-draw push: see [alerts](#alerts) |
| `[server]` | address, port, token: see [remote access](#remote-access) |
| `[meter]` | a real wall meter: see [using a real meter](#using-a-real-meter) |
| `[power]`, `[cpu]` | the model's constants: see [calibrating](#calibrating-against-reality) |
| `[sampling]` | interval and how long raw samples are kept |
| `[activity]` | what counts as "training" rather than idle but powered on |

## Electricity price

`[tariff]` covers the three ways electricity is sold; pick the one on your bill.

| `mode` | Contract | You set |
|---|---|---|
| `flat` | one price all day | `rate` |
| `tou` | fixed prices by time of day | `tou_weekday`, optionally `tou_weekend` and `tou_holidays` |
| `dynamic` | the price follows the market | `dynamic_source`, and usually `dynamic_surcharge` |

A dynamic price can come from three places:

| `dynamic_source` | Covers | Needs |
|---|---|---|
| `energy-charts` | day-ahead spot prices for European bidding zones (Fraunhofer ISE, CC BY 4.0) | `dynamic_zone`, e.g. `DE-LU` |
| `pvpc` | Spain's regulated consumer price (Red Eléctrica) | nothing |
| `http` | anywhere: the current price from any JSON endpoint, such as a Home Assistant sensor from your supplier's integration | `dynamic_url`, `dynamic_json_path` |

A spot price is not what you pay: contracts add grid fees, levies and a margin
per kWh. Put that sum in `dynamic_surcharge`. `tax_pct` is then added to every
price in the section, so leave it at 0 if the figures you typed already include
tax.

Dynamic mode needs outbound HTTP(S). While a price cannot be fetched the daemon
bills at `rate` and says so in the health banner. Each hour is stored with the
price it was billed at, so changing the tariff changes what you pay from then
on, never what a past hour cost.

## Alerts

Off until you set `alerts.idle_w`. With it set, powermon notices the machine
drawing more than that many watts **while the GPU is idle** — a stuck process, a
job you forgot — once it has lasted `after_min` (10). It sends one push, one
urgent reminder at `remind_min` (60) if it is still going, and one when it
clears; nothing in between.

Pushes go to an [ntfy](https://ntfy.sh) topic: install the app, subscribe to a
long random topic name, and put its URL in `alerts.ntfy_url`. Without a URL the
alert is still reported as `alert.active` in `/api/now`, and the menu bar app's
icon turns yellow.

```sh
curl -d "powermon test" https://ntfy.sh/<your-topic>     # check delivery
```

## Remote access

The installer already sets a token and listens on every interface, so the URL
it prints works from any device that can reach the machine. With `--local`,
powermon listens on `127.0.0.1` only and you use an SSH tunnel instead.

**The rule:** clients on the machine itself (`127.0.0.1`) never need the token,
so `pwr` keeps working unconfigured. Every other client must present it, as
`?token=…`, an `X-Powermon-Token` header, or the `powermon_token` cookie. Open
the URL with `?token=…` once and the cookie is set for a year. Listening beyond
loopback without a token is refused at startup.

The token travels over **plain HTTP**: fine on a tailnet or a trusted LAN, not
something to expose to the internet.

### Tailscale (recommended)

Puts the dashboard on a private network reachable from anywhere, with nothing
exposed to the public internet:

```sh
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Then from your laptop or phone, both on the tailnet, open
`http://<your-server>:8787/?token=<your token>`. Add it to your phone's home
screen and it behaves like an app.

### SSH tunnel

Needs no token and nothing listening remotely, so it works with `--local`:

```sh
ssh -L 8787:127.0.0.1:8787 <user>@<your-server>
```

Then open <http://localhost:8787> on your laptop.

<details>
<summary><b>Choosing a bind address</b></summary>

| `server.host` | Who can reach it |
|---|---|
| `127.0.0.1` (what `--local` sets) | this machine only — SSH tunnel for anything else |
| your Tailscale IP | the tailnet only, not the LAN |
| `0.0.0.0` | tailnet **and** LAN (token still required) |

The token is a shared secret over **plain HTTP** — fine on a tailnet or a trusted
LAN, not something to expose to the internet. For that, put Caddy in front for
TLS and real auth.

</details>

<details>
<summary><b>Named keys: one per client, revocable</b></summary>

One shared token cannot be withdrawn from a single client. Add
keys instead, and revoke them one at a time:

```sh
./powermon.py --add-key grafana --scope read    # prints the token once
./powermon.py --add-key laptop  --scope admin
./powermon.py --revoke-key grafana
systemctl --user restart powermon               # keys load at startup
```

They are stored hashed in `server.keys_file`, so that file is not itself a
credential, and the token is shown once because it is never kept. `server.token`
still works alongside them as a one-key shorthand. Every current route is
read-only, so `admin` grants nothing extra today — it exists for the first route
that changes something.

Repeated wrong keys from one address are slowed: five free attempts, then a
delay doubling to 30 s. A correct key is never delayed, even from an address
that has been guessing, so one bad client cannot lock out the rest.

</details>

<details>
<summary><b>HTTPS</b></summary>

Set `server.tls_cert` and `server.tls_key` to serve HTTPS directly. A
self-signed certificate is fine on a tailnet:

```sh
openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
  -keyout key.pem -out cert.pem -subj "/CN=$(hostname)"
```

</details>

<details>
<summary><b>Behind a reverse proxy (Caddy, nginx, Traefik)</b></summary>

Caddy, nginx and Traefik connect from this machine,
so without configuration every request they forward would look like loopback and
skip the token. Set `server.trusted_proxies` to the proxy's address and its
`X-Forwarded-For` header is used instead — from anything not listed there, the
header is ignored, so it cannot be spoofed. `server.require_token_always = true`
removes the loopback exemption entirely.

With `trusted_proxies = "127.0.0.1"` set, and powermon left on loopback:

```caddyfile
# Caddy: real certificate, powermon never exposed directly
powermon.example.com {
    reverse_proxy 127.0.0.1:8787
}
```

```nginx
# nginx: X-Forwarded-For is what powermon reads, so it must be set
location / {
    proxy_pass http://127.0.0.1:8787;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

Caddy sets `X-Forwarded-For` by default; nginx does not, so the line above is
required. Without it every proxied request arrives as bare loopback and skips
the token.

</details>

## How accurate is it

```
wall_watts = (cpu_watts + gpu_watts + baseline_watts) / psu_efficiency
```

…unless a real meter is configured, in which case the wall figure is measured
directly and the table below only decides how it is attributed. See
[using a real meter](#using-a-real-meter).

| Component | Source | Trust |
|---|---|---|
| GPU watts | every card's own sensor, summed, via NVML (`nvidia-smi` if the library is missing) | **measured**, accurate |
| CPU watts | RAPL package energy counters, all sockets, if readable | **measured** (see below) |
| CPU watts | otherwise: utilisation curve `idle_w → max_w` | **estimated**, ±20 W |
| Baseline | configured constant (board, RAM, NVMe, fans) | assumption |
| PSU loss | configured efficiency | assumption |

So the total is a **good estimate, not a metered reading**. The GPU term — the
one that actually moves during training — is real; the constant terms are where
the error lives, and they mostly cancel out when you compare one run to another.
The dashboard states which CPU source is live in the header.

### Measured CPU power

Without it the CPU term is estimated to about ±20 W. To read the real energy
counters:

```sh
~/powermon/install.sh --rapl
```

This installs `90-rapl-readable.rules`, which makes the counters readable
without root. Read the security note in that file first. Short version: the
counters were restricted because a *very* high-frequency reader can mount a
power side-channel attack against crypto on the same machine; sampling every
2 s is orders of magnitude too slow for that. Skip it on a shared host and the
estimate stays in place.

### Using a real meter

Everything above is a model. If a UPS, smart plug or metered PDU already
measures this machine, point powermon at it and the headline number becomes a
**measurement** — the model then only decides how to split that total between
CPU, GPU and the rest.

```toml
[meter]
type = "http"
http_url = "http://plug/cm?cmnd=Status%2010"
http_json_path = "StatusSNS.ENERGY.Power"
```

| Device | `http_url` | `http_json_path` |
|---|---|---|
| Tasmota | `http://plug/cm?cmnd=Status%2010` | `StatusSNS.ENERGY.Power` |
| Shelly gen1 | `http://plug/status` | `meters.0.power` |
| Shelly gen2 | `http://plug/rpc/Switch.GetStatus?id=0` | `apower` |
| Home Assistant | `http://ha:8123/api/states/sensor.rack_power` | `state` |
| UPS via NUT | `type = "nut"` | `ups.realpower`, else load × nominal |

Home Assistant needs `http_headers = "Authorization: Bearer YOUR_TOKEN"`, which
makes any energy entity it already knows about usable here — including meters
powermon has no driver for.

`power.residual_w` then reports meter minus model, in watts. That is exactly
what `baseline_w` and `psu_efficiency` are getting wrong, so you can tune them
until it approaches zero without owning a second meter.

**One caveat:** the meter measures whatever is plugged into it. If your UPS
feeds the whole rack, powermon will report the whole rack as this machine.
Use a per-outlet reading, or a plug that only this machine is on.

If the meter stops answering, powermon falls back to the model and says so
rather than reporting a gap — `power.wall_source` flips to `model` and a
warning appears on the dashboard. A dead meter is retried occasionally rather
than on every sample, so it costs nothing while it is down.

### Calibrating against reality

If you have a plug meter (a €15 one is plenty), calibrate once and every number
below gets better:

1. Idle, nothing running. Read the meter, then `pwr`.
   Set `baseline_w = meter_watts * psu_efficiency - cpu_w - gpu_w`.
2. Under a full training load, read both again. If they still disagree, adjust
   `cpu.rapl_scale` (RAPL slightly under-reads socket draw on Zen 2) or
   `power.psu_efficiency`.

`systemctl --user restart powermon` after editing. Past costs are **not**
retroactively rewritten — each hour stores the tariff it was billed at.

### Supported hosts

Only the first row has been run on real hardware; the rest is what the code
paths imply, marked as such rather than claimed.

| Host | Wall power | Verified |
|---|---|---|
| Linux + NVIDIA + readable RAPL | measured GPU, measured CPU, modelled rest | **yes** — Ryzen 9 3900X + RTX 3090, Ubuntu 22.04 |
| Linux + NVIDIA, no RAPL | measured GPU, **estimated** CPU (±20 W), modelled rest | **yes** — same box without the udev rule |
| Linux, no GPU | estimated or measured CPU, modelled rest | **yes** — same box with `nvidia-smi` hidden |
| Any host with a `[meter]` | **measured at the wall**, model only attributes it | **yes** — against a simulated plug |
| Multi-GPU / multi-socket Linux | summed across devices | code paths tested, no such hardware to hand |
| AMD or Intel GPU | not read; treated as no GPU | not implemented |
| NVIDIA MIG | reports the physical card; per-instance not attributed | untested, no MIG card to hand |
| macOS, Windows, *BSD | not supported as a *host* (the menu bar app is a client) | — |

Python 3.10 or newer, standard library only. `nvidia-smi` is optional — NVML is
used when present and is what the daemon prefers.

Without a `[meter]`, the wall figure is an **estimate**: the GPU and CPU terms
are real sensors, but `baseline_w` and `psu_efficiency` are configured
constants. On a host with no GPU and no readable RAPL, every term is modelled.

## API

| Endpoint | Returns |
|---|---|
| `GET /api/now` | current sample, period totals, 3-minute sparklines, metadata. `gpu.*` is the host aggregate across every card; `gpu.devices[]` carries each one separately |
| `GET /api/history?range=24h\|7d\|30d\|90d` | time series (24 h = 5-min averages from raw samples; longer = hourly totals) |
| `GET /healthz` | the same `health` block as `/api/now`: `ok`, `last_sample_age_s`, `coverage_today`, the live CPU/GPU sources, and an `issues` list at `error` / `warn` / `info` |

Useful for logging a run's energy cost from a training script:

```python
import urllib.request, json
def wall_watts():
    with urllib.request.urlopen("http://127.0.0.1:8787/api/now") as r:
        return json.load(r)["power"]["total_w"]

# or bracket a run and diff all-time kWh:
def kwh():
    with urllib.request.urlopen("http://127.0.0.1:8787/api/now") as r:
        return json.load(r)["totals"]["all"]["kwh"]
```

## Reference

<details>
<summary><b>Storage, and why history is never rewritten</b></summary>

Two tables in `powermon.db`:

- `samples` — every 2 s reading, pruned after `raw_retention_days` (7 → ~30 MB).
- `hourly` — Wh, cost, busy/idle split, peak watts per hour. **Kept forever**,
  about 1 MB per year. This is what the long-range views and lifetime totals
  read, so deleting raw samples never loses your cost history.

Energy is integrated with the actual gap between samples, and gaps longer than
`gap_max_s` (60 s) are skipped — so time when the service was down is not
silently billed as if the machine had been drawing its last known power.

**Editing your tariff does not rewrite history.** Each hour stores the energy
rate it was billed at, and each day stores the standing charge in effect that
day, in a third `days` table. Changing `config.toml` changes what you pay from
then on, not what a closed day already cost. Today, month and all time apply the
same rule — the charge for every day actually recorded — so "all time" now means
all electricity, which it previously did not. Days the service was not running
are not charged, matching `hours` being recorded rather than elapsed time.
`cost` is split into `energy_cost` and `standing_cost` in the API. Standing
charges accrue from this version onward; days recorded before it have no stored
charge and contribute nothing.

**Sensor failures reduce coverage, never consumption.** If a GPU read fails —
a driver hiccup, a timeout — that interval contributes no energy at all and is
recorded as missing time in `secs_missing` instead. The sample stores NULL
rather than 0 W, so history shows a gap instead of a dip, and `/api/now`
reports `power.partial` for the live reading plus a `coverage` figure on every
totals period. A silently cheaper month would be worse than a visible hole. A
machine with no GPU is not a failure: that is a known zero and is billed
normally.

</details>

<details>
<summary><b>Operating the service</b></summary>

```sh
systemctl --user status powermon
systemctl --user restart powermon
journalctl --user -u powermon -f
```

The sampler catches per-sample failures and keeps running: an `nvidia-smi`
hiccup records the GPU as unavailable for that sample instead of killing the
service, and systemd restarts the process if it ever does die.

</details>

<details>
<summary><b>Files in this repository</b></summary>

| File | |
|---|---|
| `powermon.py` | sampler + HTTP API + static server — standard library only, no pip installs (running on system `/usr/bin/python3` 3.10, so conda envs can't break it) |
| `dashboard.html` | the UI, self-contained, no external requests |
| `pwr` | terminal client (symlinked into `~/.local/bin`) |
| `config.toml` | all settings |
| `90-rapl-readable.rules` | optional udev rule for measured CPU power |
| `tests/` | regression tests — `python3 -m unittest discover -s tests`, standard library only |
| `CHANGELOG.md` | what changed per release |
| `CONTRIBUTING.md` | how to run the tests, and the two constraints to know first |
| `LICENSE` | MIT |
| `dev.py` | serves the local `dashboard.html` against a running powermon and reloads on save, for working on the UI |
| `install.sh` | sets all of this up as a systemd user service |
| `systemd/powermon.service` | unit, installed to `~/.config/systemd/user/` |

</details>

## License

[MIT](LICENSE). See [CONTRIBUTING.md](CONTRIBUTING.md) for running the tests
and [CHANGELOG.md](CHANGELOG.md) for what changed.
