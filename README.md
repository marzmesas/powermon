# powermon

Real-time power, resource and cost monitor for this training box
(Ryzen 9 3900X + RTX 3090, Ubuntu 22.04).

Runs as a systemd **user** service, samples every 2 s, keeps history in SQLite,
and serves a dashboard plus a JSON API on `127.0.0.1:8787`.

- **Dashboard** — <http://localhost:8787>
- **Terminal** — `pwr` (snapshot) · `pwr -w` (live) · `pwr --json`
- **Overhead** — 0.2 % of one core, 22 MB RAM. Runs at `Nice=10` with idle I/O
  priority so it never competes with a training job.

## Install

Standard library Python only — nothing to `pip install`.

```sh
git clone https://github.com/marzmesas/powermon.git ~/powermon
cd ~/powermon
cp config.example.toml config.toml     # then edit tariff.rate
sed -e "s|/home/marzmesas|$HOME|g" systemd/powermon.service \
    > ~/.config/systemd/user/powermon.service
systemctl --user daemon-reload
systemctl --user enable --now powermon
ln -sf ~/powermon/pwr ~/.local/bin/pwr
sudo loginctl enable-linger "$USER"    # so it survives logout and starts at boot
```

Open <http://localhost:8787>. Works without a GPU (GPU panels hide themselves)
and without RAPL (CPU power falls back to a utilisation model).

## Where the numbers come from

```
wall_watts = (cpu_watts + gpu_watts + baseline_watts) / psu_efficiency
```

| Component | Source | Trust |
|---|---|---|
| GPU watts | the card's own sensor, via `nvidia-smi` | **measured**, accurate |
| CPU watts | RAPL package energy counter, if readable | **measured** (see below) |
| CPU watts | otherwise: utilisation curve `idle_w → max_w` | **estimated**, ±20 W |
| Baseline | configured constant (board, RAM, NVMe, fans) | assumption |
| PSU loss | configured efficiency | assumption |

So the total is a **good estimate, not a metered reading**. The GPU term — the
one that actually moves during training — is real; the constant terms are where
the error lives, and they mostly cancel out when you compare one run to another.
The dashboard states which CPU source is live in the header.

### Two things worth doing

**1. Enable measured CPU power** (turns "estimated" into "measured"):

```sh
sudo cp ~/powermon/90-rapl-readable.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger --subsystem-match=powercap
systemctl --user restart powermon
```

Read the security note in that rules file first. Short version: these counters
were restricted because a *very* high-frequency reader can mount a power
side-channel attack against crypto on the same machine; sampling every 2 s is
orders of magnitude too slow to matter, and this is a single-user box. Skip it
on a shared host and the estimate stays in place.

**2. Survive logout / reboot — done.** Lingering is enabled
(`loginctl show-user marzmesas -p Linger` → `Linger=yes`), so the service starts
at boot and keeps running with nobody logged in.

Without it, a systemd *user* service only lives as long as a login session, and
this box would have been relying on GDM auto-login to start it — a coincidence
that would break the day auto-login is turned off or the desktop is removed. If
you ever rebuild this machine, re-run:

```sh
sudo loginctl enable-linger marzmesas
```

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

## Configuration

Everything lives in `config.toml`. The one you'll actually change:

```toml
[tariff]
rate = 0.15        # your price per kWh
```

Other keys: `server.host` / `server.port` / `server.token` (see
[remote access](#reaching-it-from-your-laptop-or-phone)),
`sampling.interval`, `sampling.raw_retention_days`,
`power.baseline_w`, `power.psu_efficiency`, `cpu.*`, `activity.*` (what counts
as "training" rather than idle-but-powered-on).

## Reaching it from your laptop or phone

The service listens on `0.0.0.0`, guarded by `server.token`.

**Access rule:** loopback clients (`127.0.0.1`) never need the token, so `pwr` and
anything else on this box keeps working unconfigured. Every other client must
present it, as `?token=…`, an `X-Powermon-Token` header, or the `powermon_token`
cookie. Open the URL with `?token=…` once and the cookie is set for a year, so
the token stops showing up in the address bar.

### Tailscale (recommended)

Puts the dashboard on a private network reachable from anywhere, with nothing
exposed to the public internet:

```sh
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
tailscale ip -4          # the address to use
```

Then from your Mac or phone (both on the tailnet):
`http://<tailscale-ip>:8787/?token=<your token>` — or use the MagicDNS name,
`http://<your-server>:8787/?token=…`. Add it to your phone's home screen
and it behaves like an app.

### SSH tunnel (no install, nothing listening remotely)

```sh
ssh -L 8787:127.0.0.1:8787 <user>@<your-server>
```

Then open `http://localhost:8787` on the Mac. The tunnel arrives as loopback, so
**no token is needed** this way.

### Locking it down further

`0.0.0.0` also means your LAN can reach the port (token still required). To bind
only to the tailnet, set `server.host` to your Tailscale IP. To go back to
loopback-only, set it to `127.0.0.1` and use the SSH tunnel.

The token is a shared secret over **plain HTTP** — fine on a tailnet or a trusted
LAN, not something to expose to the internet. For that, put Caddy in front for
TLS and real auth.

## What the dashboard shows

- **Hero** — watts at the wall right now, cost per hour, cost if it ran all month.
- **Meters** — GPU power against its 350 W limit, GPU load / memory, CPU power,
  RAM, disk.
- **Tiles** — today, month to date, projected month, **idle share** (what you
  paid to leave it powered on and not training), all time.
- **Charts** — power draw stacked by component, kWh + cost per hour/day,
  temperatures. 24 h / 7 d / 30 d. Every chart has a table view and a tooltip;
  arrow keys move the crosshair.
- **GPU processes** — what's actually on the card.

## API

| Endpoint | Returns |
|---|---|
| `GET /api/now` | current sample, period totals, 3-minute sparklines, metadata |
| `GET /api/history?range=24h\|7d\|30d\|90d` | time series (24 h = 5-min averages from raw samples; longer = hourly totals) |
| `GET /healthz` | `{"ok": true, "last_sample_age_s": …}` |

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

## Storage

Two tables in `powermon.db`:

- `samples` — every 2 s reading, pruned after `raw_retention_days` (7 → ~30 MB).
- `hourly` — Wh, cost, busy/idle split, peak watts per hour. **Kept forever**,
  about 1 MB per year. This is what the long-range views and lifetime totals
  read, so deleting raw samples never loses your cost history.

Energy is integrated with the actual gap between samples, and gaps longer than
`gap_max_s` (60 s) are skipped — so time when the service was down is not
silently billed as if the machine had been drawing its last known power.

## Operating

```sh
systemctl --user status powermon
systemctl --user restart powermon
journalctl --user -u powermon -f
```

The sampler catches per-sample failures and keeps running: an `nvidia-smi`
hiccup records the GPU as unavailable for that sample instead of killing the
service, and systemd restarts the process if it ever does die.

## Files

| File | |
|---|---|
| `powermon.py` | sampler + HTTP API + static server — standard library only, no pip installs (running on system `/usr/bin/python3` 3.10, so conda envs can't break it) |
| `dashboard.html` | the UI, self-contained, no external requests |
| `pwr` | terminal client (symlinked into `~/.local/bin`) |
| `config.toml` | all settings |
| `90-rapl-readable.rules` | optional udev rule for measured CPU power |
| `systemd/powermon.service` | unit, installed to `~/.config/systemd/user/` |
