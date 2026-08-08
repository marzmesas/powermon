#!/usr/bin/env python3
"""powermon - real-time power, resource and cost monitor for a single training box.

One process: a sampler thread writing to SQLite, plus an HTTP server exposing a
JSON API and the dashboard. Standard library only.

Power model
-----------
  wall_w = (cpu_w + gpu_w + baseline_w) / psu_efficiency

  cpu_w  measured from the RAPL package energy counter when readable, otherwise
         estimated from utilisation (flagged as "estimated" everywhere).
  gpu_w  measured by nvidia-smi (real on-board sensor).
  baseline_w  configured constant for board/RAM/NVMe/fans.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
DEFAULTS = {
    "server": {"host": "127.0.0.1", "port": 8787, "token": ""},
    "sampling": {"interval": 2.0, "raw_retention_days": 7, "gap_max_s": 60.0},
    "tariff": {"currency": "EUR", "symbol": "EUR", "mode": "flat",
               "rate": 0.15, "standing_charge_per_day": 0.0},
    "power": {"psu_efficiency": 0.90, "baseline_w": 35.0},
    "cpu": {"rapl_scale": 1.0, "idle_w": 30.0, "max_w": 142.0, "curve_exp": 1.25},
    "gpu": {"enabled": True},
    "activity": {"busy_gpu_util": 15.0, "busy_gpu_power_w": 80.0},
}


# --------------------------------------------------------------------------- config


def _parse_toml_subset(text: str) -> dict:
    """Minimal [section] key = value parser, for interpreters without tomllib."""
    out: dict = {}
    section = out
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = out.setdefault(line[1:-1].strip(), {})
            continue
        if "=" not in line:
            continue
        key, val = (p.strip() for p in line.split("=", 1))
        if val.lower() in ("true", "false"):
            parsed: object = val.lower() == "true"
        elif val[:1] in ("'", '"'):
            parsed = val[1:-1]
        else:
            try:
                parsed = float(val) if ("." in val or "e" in val.lower()) else int(val)
            except ValueError:
                parsed = val
        section[key] = parsed
    return out


def load_config(path: Path) -> dict:
    cfg = {k: dict(v) for k, v in DEFAULTS.items()}
    if path.exists():
        text = path.read_text(encoding="utf-8")
        try:
            import tomllib

            user = tomllib.loads(text)
        except ModuleNotFoundError:
            user = _parse_toml_subset(text)
        for section, values in user.items():
            if isinstance(values, dict):
                cfg.setdefault(section, {}).update(values)
    return cfg


# --------------------------------------------------------------------------- readers


class CpuReader:
    """CPU utilisation, temperature and power.

    Power comes from the RAPL package energy counter if it is readable; the
    fallback is a utilisation curve between idle_w and max_w.
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._stat: tuple[int, int] | None = None
        self._rapl_path: Path | None = None
        self._rapl_max = 0
        self._rapl_prev: tuple[float, int] | None = None
        self.source = "estimated"
        self._find_rapl()
        self._temp_path = self._find_cpu_temp()
        self.ncpu = os.cpu_count() or 1

    def _find_rapl(self) -> None:
        for dom in sorted(Path("/sys/class/powercap").glob("*-rapl:[0-9]")):
            name_file, energy = dom / "name", dom / "energy_uj"
            try:
                if not name_file.read_text().strip().startswith("package"):
                    continue
                energy.read_text()  # permission probe
            except OSError:
                continue
            self._rapl_path = energy
            try:
                self._rapl_max = int((dom / "max_energy_range_uj").read_text())
            except OSError:
                self._rapl_max = 2**32
            self.source = "rapl"
            return

    @staticmethod
    def _find_cpu_temp() -> Path | None:
        preferred = ("Tdie", "Tctl", "Package id 0", "Tccd1")
        found: dict[str, Path] = {}
        for hwmon in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
            try:
                chip = (hwmon / "name").read_text().strip()
            except OSError:
                continue
            if chip not in ("k10temp", "coretemp", "zenpower"):
                continue
            for label_file in sorted(hwmon.glob("temp*_label")):
                try:
                    found.setdefault(label_file.read_text().strip(),
                                     label_file.with_name(label_file.name.replace("_label", "_input")))
                except OSError:
                    pass
            if not found and (hwmon / "temp1_input").exists():
                found["temp1"] = hwmon / "temp1_input"
        for key in preferred:
            if key in found:
                return found[key]
        return next(iter(found.values()), None)

    def util(self) -> float:
        """Percent busy since the previous call."""
        try:
            fields = Path("/proc/stat").read_text().split("\n", 1)[0].split()[1:]
        except OSError:
            return 0.0
        values = [int(v) for v in fields]
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        total = sum(values)
        prev, self._stat = self._stat, (total, idle)
        if prev is None:
            return 0.0
        d_total, d_idle = total - prev[0], idle - prev[1]
        if d_total <= 0:
            return 0.0
        return max(0.0, min(100.0, 100.0 * (d_total - d_idle) / d_total))

    def temp(self) -> float | None:
        if not self._temp_path:
            return None
        try:
            return int(self._temp_path.read_text()) / 1000.0
        except OSError:
            return None

    def power(self, util_pct: float, now: float) -> float:
        if self._rapl_path is not None:
            try:
                energy = int(self._rapl_path.read_text())
            except OSError:
                self._rapl_path, self.source = None, "estimated"
                return self._estimate(util_pct)
            prev, self._rapl_prev = self._rapl_prev, (now, energy)
            if prev is not None:
                dt = now - prev[0]
                d_uj = energy - prev[1]
                if d_uj < 0:  # counter wrapped
                    d_uj += self._rapl_max
                if dt > 0:
                    watts = (d_uj / 1e6) / dt * float(self.cfg["cpu"]["rapl_scale"])
                    if 0 <= watts < 1000:
                        return watts
            return self._estimate(util_pct)
        return self._estimate(util_pct)

    def _estimate(self, util_pct: float) -> float:
        c = self.cfg["cpu"]
        idle_w, max_w = float(c["idle_w"]), float(c["max_w"])
        return idle_w + (max_w - idle_w) * (max(0.0, util_pct) / 100.0) ** float(c["curve_exp"])

    @staticmethod
    def freq_mhz() -> float | None:
        try:
            vals = [float(m) for m in re.findall(r"cpu MHz\s*:\s*([\d.]+)",
                                                Path("/proc/cpuinfo").read_text())]
            return sum(vals) / len(vals) if vals else None
        except OSError:
            return None


class GpuReader:
    """NVIDIA GPU sampling via nvidia-smi."""

    FIELDS = ("power.draw", "utilization.gpu", "memory.used", "memory.total",
              "temperature.gpu", "fan.speed", "clocks.sm")

    def __init__(self, cfg: dict):
        self.enabled = bool(cfg["gpu"]["enabled"]) and shutil.which("nvidia-smi") is not None
        self.name = None
        self.power_limit = 350.0
        self.fail_count = 0
        if self.enabled:
            info = self._run(["--query-gpu=name,enforced.power.limit", "--format=csv,noheader,nounits"])
            if info:
                parts = info[0].split(", ")
                self.name = parts[0]
                try:
                    self.power_limit = float(parts[1])
                except (IndexError, ValueError):
                    pass

    @staticmethod
    def _run(args: list[str]) -> list[str] | None:
        try:
            out = subprocess.run(["nvidia-smi", *args], capture_output=True, text=True,
                                 timeout=10, check=True).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        return [ln for ln in (l.strip() for l in out.splitlines()) if ln]

    def sample(self) -> dict:
        """Never raises: a driver hiccup yields None values, not a dead sampler."""
        if not self.enabled:
            return {"present": False}
        rows = self._run([f"--query-gpu={','.join(self.FIELDS)}", "--format=csv,noheader,nounits"])
        if not rows:
            self.fail_count += 1
            return {"present": True, "error": True}
        self.fail_count = 0
        vals: list[float | None] = []
        for item in rows[0].split(", "):
            try:
                vals.append(float(item))
            except ValueError:
                vals.append(None)
        power, util, mem_used, mem_total, temp, fan, clock = (vals + [None] * 7)[:7]
        procs = []
        app_rows = self._run(["--query-compute-apps=pid,process_name,used_memory",
                              "--format=csv,noheader,nounits"]) or []
        for row in app_rows[:6]:
            parts = row.split(", ")
            if len(parts) >= 3:
                procs.append({"pid": parts[0],
                              "name": Path(parts[1]).name[:40],
                              "mem_mib": _num(parts[2])})
        return {"present": True, "power_w": power, "util": util, "mem_used": mem_used,
                "mem_total": mem_total, "temp": temp, "fan": fan, "clock_mhz": clock,
                "procs": procs, "limit_w": self.power_limit, "name": self.name}


def _num(text: str) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def read_mem() -> dict:
    info = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            info[key] = float(rest.strip().split()[0]) / (1024 * 1024)  # GiB
    except OSError:
        return {"total_gib": 0.0, "used_gib": 0.0, "pct": 0.0}
    total = info.get("MemTotal", 0.0)
    avail = info.get("MemAvailable", total)
    return {"total_gib": total, "used_gib": total - avail,
            "pct": (100.0 * (total - avail) / total) if total else 0.0}


class NetReader:
    def __init__(self) -> None:
        self._prev: tuple[float, int, int] | None = None

    def sample(self, now: float) -> dict:
        rx = tx = 0
        try:
            for line in Path("/proc/net/dev").read_text().splitlines()[2:]:
                iface, _, rest = line.partition(":")
                if iface.strip() in ("lo",):
                    continue
                cols = rest.split()
                rx += int(cols[0])
                tx += int(cols[8])
        except (OSError, IndexError, ValueError):
            return {"rx_mbps": 0.0, "tx_mbps": 0.0}
        prev, self._prev = self._prev, (now, rx, tx)
        if prev is None or now <= prev[0]:
            return {"rx_mbps": 0.0, "tx_mbps": 0.0}
        dt = now - prev[0]
        return {"rx_mbps": max(0.0, (rx - prev[1]) * 8 / 1e6 / dt),
                "tx_mbps": max(0.0, (tx - prev[2]) * 8 / 1e6 / dt)}


def uptime_s() -> float:
    try:
        return float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError):
        return 0.0


def loadavg() -> list[float]:
    try:
        return [float(v) for v in Path("/proc/loadavg").read_text().split()[:3]]
    except (OSError, ValueError):
        return [0.0, 0.0, 0.0]


# --------------------------------------------------------------------------- storage

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
  ts REAL PRIMARY KEY, total_w REAL, cpu_w REAL, gpu_w REAL, other_w REAL,
  cpu_pct REAL, ram_pct REAL, gpu_util REAL, gpu_mem_pct REAL,
  cpu_temp REAL, gpu_temp REAL, busy INTEGER
);
CREATE TABLE IF NOT EXISTS hourly (
  hour INTEGER PRIMARY KEY, wh REAL, wh_busy REAL, wh_idle REAL,
  gpu_wh REAL, cpu_wh REAL, other_wh REAL,
  cost REAL, rate REAL, secs REAL, max_w REAL
);
CREATE INDEX IF NOT EXISTS samples_ts ON samples (ts);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.lock:
            self.db.executescript(SCHEMA)
            self.db.execute("PRAGMA journal_mode=WAL")
            # ~43k commits/day: NORMAL skips an fsync per commit, risking only the
            # last few seconds of samples on a power cut. Right trade for telemetry.
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.execute("PRAGMA wal_autocheckpoint=256")
            self.db.commit()

    def add_sample(self, s: dict) -> None:
        with self.lock:
            self.db.execute(
                "INSERT OR REPLACE INTO samples VALUES "
                "(:ts,:total_w,:cpu_w,:gpu_w,:other_w,:cpu_pct,:ram_pct,"
                ":gpu_util,:gpu_mem_pct,:cpu_temp,:gpu_temp,:busy)", s)
            self.db.commit()

    def add_energy(self, t0: float, t1: float, watts: dict, busy: bool, rate: float) -> None:
        """Integrate watts over [t0, t1), splitting across hour boundaries."""
        with self.lock:
            while t0 < t1:
                hour = int(t0 // 3600) * 3600
                seg_end = min(t1, hour + 3600)
                dt = seg_end - t0
                factor = dt / 3600.0
                wh = watts["total"] * factor
                self.db.execute(
                    "INSERT INTO hourly (hour, wh, wh_busy, wh_idle, gpu_wh, cpu_wh, other_wh,"
                    " cost, rate, secs, max_w) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(hour) DO UPDATE SET "
                    " wh=wh+excluded.wh, wh_busy=wh_busy+excluded.wh_busy,"
                    " wh_idle=wh_idle+excluded.wh_idle, gpu_wh=gpu_wh+excluded.gpu_wh,"
                    " cpu_wh=cpu_wh+excluded.cpu_wh, other_wh=other_wh+excluded.other_wh,"
                    " cost=cost+excluded.cost, rate=excluded.rate, secs=secs+excluded.secs,"
                    " max_w=MAX(max_w, excluded.max_w)",
                    (hour, wh, wh if busy else 0.0, 0.0 if busy else wh,
                     watts["gpu"] * factor, watts["cpu"] * factor, watts["other"] * factor,
                     wh / 1000.0 * rate, rate, dt, watts["total"]))
                t0 = seg_end
            self.db.commit()

    def prune(self, older_than_ts: float) -> None:
        with self.lock:
            self.db.execute("DELETE FROM samples WHERE ts < ?", (older_than_ts,))
            self.db.commit()

    def query(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute(sql, args).fetchall()


# --------------------------------------------------------------------------- sampler


class Monitor:
    def __init__(self, cfg: dict, store: Store):
        self.cfg = cfg
        self.store = store
        self.cpu = CpuReader(cfg)
        self.gpu = GpuReader(cfg)
        self.net = NetReader()
        self.latest: dict = {}
        self.recent: list[dict] = []       # in-memory ring for sparklines
        self.started = time.time()
        self.session_wh = 0.0
        self._prev_ts: float | None = None
        self._last_prune = 0.0
        self._stop = threading.Event()

    @property
    def rate(self) -> float:
        return float(self.cfg["tariff"]["rate"]) if self.cfg["tariff"]["mode"] != "none" else 0.0

    def sample_once(self) -> dict:
        now = time.time()
        util = self.cpu.util()
        cpu_w = self.cpu.power(util, now)
        gpu = self.gpu.sample()
        gpu_w = gpu.get("power_w") or 0.0
        eff = max(0.5, min(1.0, float(self.cfg["power"]["psu_efficiency"])))
        dc = cpu_w + gpu_w + float(self.cfg["power"]["baseline_w"])
        total_w = dc / eff
        other_w = max(0.0, total_w - cpu_w - gpu_w)

        act = self.cfg["activity"]
        busy = bool((gpu.get("util") or 0) >= float(act["busy_gpu_util"])
                    or gpu_w >= float(act["busy_gpu_power_w"]))
        mem = read_mem()
        mem_total = gpu.get("mem_total") or 0.0
        gpu_mem_pct = 100.0 * (gpu.get("mem_used") or 0.0) / mem_total if mem_total else 0.0

        row = {"ts": now, "total_w": total_w, "cpu_w": cpu_w, "gpu_w": gpu_w,
               "other_w": other_w, "cpu_pct": util, "ram_pct": mem["pct"],
               "gpu_util": gpu.get("util") or 0.0, "gpu_mem_pct": gpu_mem_pct,
               "cpu_temp": self.cpu.temp(), "gpu_temp": gpu.get("temp"),
               "busy": int(busy)}
        self.store.add_sample(row)

        # energy integration, skipping service-downtime gaps
        gap_max = float(self.cfg["sampling"]["gap_max_s"])
        if self._prev_ts is not None and 0 < now - self._prev_ts <= gap_max:
            watts = {"total": total_w, "cpu": cpu_w, "gpu": gpu_w, "other": other_w}
            self.store.add_energy(self._prev_ts, now, watts, busy, self.rate)
            self.session_wh += total_w * (now - self._prev_ts) / 3600.0
        self._prev_ts = now

        detail = dict(row)
        detail.update({"gpu": gpu, "mem": mem, "net": self.net.sample(now),
                       "load": loadavg(), "cpu_freq": self.cpu.freq_mhz(),
                       "disk": self._disk(), "cpu_power_source": self.cpu.source})
        self.latest = detail
        self.recent.append({"ts": now, "total_w": total_w, "cpu_w": cpu_w, "gpu_w": gpu_w,
                            "gpu_temp": gpu.get("temp"), "cpu_temp": row["cpu_temp"],
                            "cpu_pct": util, "gpu_util": row["gpu_util"]})
        del self.recent[:-180]
        return detail

    @staticmethod
    def _disk() -> dict:
        try:
            usage = shutil.disk_usage(os.path.expanduser("~"))
        except OSError:
            return {"used_gib": 0.0, "total_gib": 0.0, "pct": 0.0}
        gib = 1024 ** 3
        return {"used_gib": usage.used / gib, "total_gib": usage.total / gib,
                "pct": 100.0 * usage.used / usage.total if usage.total else 0.0}

    def run(self) -> None:
        interval = max(0.5, float(self.cfg["sampling"]["interval"]))
        while not self._stop.is_set():
            cycle_start = time.time()
            try:
                self.sample_once()
            except Exception as exc:  # a bad sample must never kill the loop
                print(f"powermon: sample failed: {exc!r}", file=sys.stderr, flush=True)
            if cycle_start - self._last_prune > 3600:
                self._last_prune = cycle_start
                try:
                    days = float(self.cfg["sampling"]["raw_retention_days"])
                    self.store.prune(cycle_start - days * 86400)
                except Exception as exc:
                    print(f"powermon: prune failed: {exc!r}", file=sys.stderr, flush=True)
            self._stop.wait(max(0.05, interval - (time.time() - cycle_start)))

    def stop(self) -> None:
        self._stop.set()


# --------------------------------------------------------------------------- totals


def _day_start(dt: datetime) -> float:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _month_start(dt: datetime) -> float:
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()


def totals(store: Store, cfg: dict) -> dict:
    now = datetime.now()
    standing = float(cfg["tariff"].get("standing_charge_per_day", 0.0) or 0.0)
    out = {}
    windows = {"today": (_day_start(now), 1),          # standing charge: 1 day
               "month": (_month_start(now), now.day),   # ... x days elapsed this month
               "all": (0.0, None)}                      # ... not applied to all-time
    for label, (start, days) in windows.items():
        row = store.query(
            "SELECT COALESCE(SUM(wh),0) wh, COALESCE(SUM(wh_busy),0) busy,"
            " COALESCE(SUM(wh_idle),0) idle, COALESCE(SUM(cost),0) cost,"
            " COALESCE(MAX(max_w),0) peak, COALESCE(SUM(secs),0) secs"
            " FROM hourly WHERE hour >= ?", (int(start),))[0]
        cost = row["cost"] + (standing * days if days else 0.0)
        out[label] = {"kwh": row["wh"] / 1000.0, "busy_kwh": row["busy"] / 1000.0,
                      "idle_kwh": row["idle"] / 1000.0, "cost": cost,
                      "peak_w": row["peak"], "hours": row["secs"] / 3600.0}
    # Projection: the average draw over the time actually recorded, extended to a
    # 30-day month. Deliberately not scaled by elapsed calendar days -- in a month
    # where recording started on the 28th that would divide by ~28 and read near zero.
    recorded_h = out["month"]["hours"]
    rate = float(cfg["tariff"]["rate"]) if cfg["tariff"]["mode"] != "none" else 0.0
    if recorded_h > 0.01:
        avg_kw = out["month"]["kwh"] / recorded_h
        projected_kwh = avg_kw * 24.0 * 30.0
        out["month"]["projected_30d_kwh"] = projected_kwh
        out["month"]["projected_30d_cost"] = projected_kwh * rate + standing * 30.0
    else:
        out["month"]["projected_30d_kwh"] = 0.0
        out["month"]["projected_30d_cost"] = 0.0
    return out


def history(store: Store, monitor: Monitor, span: str) -> dict:
    """Time series for the chart. 24h reads raw samples; longer spans read hourly."""
    now = time.time()
    if span == "24h":
        start = now - 86400
        rows = store.query(
            "SELECT ts, total_w, cpu_w, gpu_w, other_w, gpu_temp, cpu_temp, busy"
            " FROM samples WHERE ts >= ? ORDER BY ts", (start,))
        bucket = 300.0  # 5 min -> 288 points
        buckets: dict[int, dict] = {}
        for r in rows:
            key = int(r["ts"] // bucket)
            b = buckets.setdefault(key, {"n": 0, "total": 0.0, "cpu": 0.0, "gpu": 0.0,
                                         "other": 0.0, "gt": [], "ct": [], "busy": 0})
            b["n"] += 1
            b["total"] += r["total_w"]
            b["cpu"] += r["cpu_w"]
            b["gpu"] += r["gpu_w"]
            b["other"] += r["other_w"]
            b["busy"] += r["busy"]
            if r["gpu_temp"] is not None:
                b["gt"].append(r["gpu_temp"])
            if r["cpu_temp"] is not None:
                b["ct"].append(r["cpu_temp"])
        series = {k: [] for k in ("t", "total", "cpu", "gpu", "other",
                                  "gpu_temp", "cpu_temp", "busy_frac", "kwh")}
        for key in sorted(buckets):
            b = buckets[key]
            n = b["n"]
            series["t"].append(key * bucket)
            series["total"].append(b["total"] / n)
            series["cpu"].append(b["cpu"] / n)
            series["gpu"].append(b["gpu"] / n)
            series["other"].append(b["other"] / n)
            series["gpu_temp"].append(sum(b["gt"]) / len(b["gt"]) if b["gt"] else None)
            series["cpu_temp"].append(sum(b["ct"]) / len(b["ct"]) if b["ct"] else None)
            series["busy_frac"].append(b["busy"] / n)
            series["kwh"].append(b["total"] / n * bucket / 3600.0 / 1000.0)
        series["bucket_s"] = bucket
        return series

    days = {"7d": 7, "30d": 30, "90d": 90}.get(span, 7)
    rows = store.query(
        "SELECT hour, wh, gpu_wh, cpu_wh, other_wh, cost, secs, max_w, wh_busy"
        " FROM hourly WHERE hour >= ? ORDER BY hour", (int(now - days * 86400),))
    series = {k: [] for k in ("t", "total", "cpu", "gpu", "other",
                              "gpu_temp", "cpu_temp", "busy_frac", "kwh", "cost")}
    for r in rows:
        hours = max(1e-9, r["secs"] / 3600.0)
        series["t"].append(r["hour"])
        series["total"].append(r["wh"] / hours)
        series["cpu"].append(r["cpu_wh"] / hours)
        series["gpu"].append(r["gpu_wh"] / hours)
        series["other"].append(r["other_wh"] / hours)
        series["gpu_temp"].append(None)
        series["cpu_temp"].append(None)
        series["busy_frac"].append(r["wh_busy"] / r["wh"] if r["wh"] else 0.0)
        series["kwh"].append(r["wh"] / 1000.0)
        series["cost"].append(r["cost"])
    series["bucket_s"] = 3600.0
    return series


def now_payload(monitor: Monitor, store: Store, cfg: dict) -> dict:
    latest = dict(monitor.latest)
    if not latest:
        latest = monitor.sample_once()
    gpu = latest.get("gpu", {})
    rate = monitor.rate
    tariff = cfg["tariff"]
    spark = {"ts": [], "total": [], "gpu": [], "cpu": [], "gpu_temp": [], "cpu_temp": []}
    for r in monitor.recent[-90:]:
        spark["ts"].append(r["ts"])
        spark["total"].append(r["total_w"])
        spark["gpu"].append(r["gpu_w"])
        spark["cpu"].append(r["cpu_w"])
        spark["gpu_temp"].append(r["gpu_temp"])
        spark["cpu_temp"].append(r["cpu_temp"])
    return {
        "ts": latest["ts"],
        "power": {"total_w": latest["total_w"], "cpu_w": latest["cpu_w"],
                  "gpu_w": latest["gpu_w"], "other_w": latest["other_w"],
                  "cost_per_h": latest["total_w"] / 1000.0 * rate,
                  "source": latest.get("cpu_power_source", "estimated")},
        "cpu": {"pct": latest["cpu_pct"], "temp": latest["cpu_temp"],
                "freq_mhz": latest.get("cpu_freq"), "load": latest.get("load"),
                "cores": monitor.cpu.ncpu, "max_w": float(cfg["cpu"]["max_w"])},
        "gpu": {"present": gpu.get("present", False), "name": gpu.get("name"),
                "util": gpu.get("util"), "temp": gpu.get("temp"), "fan": gpu.get("fan"),
                "clock_mhz": gpu.get("clock_mhz"), "mem_used": gpu.get("mem_used"),
                "mem_total": gpu.get("mem_total"), "limit_w": gpu.get("limit_w"),
                "procs": gpu.get("procs", []), "error": gpu.get("error", False)},
        "mem": latest.get("mem", {}), "disk": latest.get("disk", {}),
        "net": latest.get("net", {}), "busy": bool(latest["busy"]),
        "totals": totals(store, cfg),
        "session": {"wh": monitor.session_wh, "cost": monitor.session_wh / 1000.0 * rate,
                    "seconds": time.time() - monitor.started},
        "spark": spark,
        "meta": {"symbol": tariff["symbol"], "currency": tariff["currency"],
                 "rate": rate, "mode": tariff["mode"],
                 "baseline_w": float(cfg["power"]["baseline_w"]),
                 "psu_efficiency": float(cfg["power"]["psu_efficiency"]),
                 "interval": float(cfg["sampling"]["interval"]),
                 "host": socket.gethostname(), "uptime_s": uptime_s(),
                 "standing_charge_per_day": float(tariff.get("standing_charge_per_day", 0.0) or 0.0)},
    }


# --------------------------------------------------------------------------- http


class Handler(BaseHTTPRequestHandler):
    server_version = "powermon"
    monitor: Monitor
    store: Store
    cfg: dict

    def log_message(self, fmt: str, *args) -> None:  # quiet by default
        if os.environ.get("POWERMON_ACCESS_LOG"):
            super().log_message(fmt, *args)

    def _send(self, code: int, body: bytes, ctype: str, cache: str = "no-store",
              extra: list[tuple[str, str]] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        for key, val in (extra or []):
            self.send_header(key, val)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    # -- access control -----------------------------------------------------
    # A token is required only for non-loopback clients, so `pwr` and anything
    # else on this host keeps working with no configuration. Set
    # server.token in config.toml before binding to 0.0.0.0.
    def _local(self) -> bool:
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def _check_token(self, route) -> tuple[bool, bool]:
        """Returns (authorised, token_came_from_query)."""
        token = str(self.cfg["server"].get("token") or "")
        if not token or self._local():
            return True, False
        from_query = False
        supplied = None
        values = parse_qs(route.query).get("token")
        if values:
            supplied, from_query = values[0], True
        if supplied is None:
            supplied = self.headers.get("X-Powermon-Token")
        if supplied is None:
            for part in (self.headers.get("Cookie") or "").split(";"):
                key, _, val = part.strip().partition("=")
                if key == "powermon_token":
                    supplied = val
                    break
        if supplied is None:
            return False, False
        # Compare as bytes: compare_digest raises TypeError on non-ASCII str, which a
        # hand-typed token in a URL could easily be.
        return hmac.compare_digest(supplied.encode("utf-8", "replace"),
                                   token.encode("utf-8", "replace")), from_query

    DENIED = (b"<!doctype html><meta charset=utf-8><title>powermon</title>"
              b"<style>body{font:15px/1.5 system-ui;margin:14vh auto;max-width:30em;padding:0 1.2em;"
              b"color:#0b0b0b;background:#f9f9f7}code{background:#eee;padding:1px 5px;border-radius:4px}"
              b"@media(prefers-color-scheme:dark){body{color:#fff;background:#0d0d0d}"
              b"code{background:#2c2c2a}}</style><h1>powermon</h1>"
              b"<p>This dashboard needs an access token. Open it as "
              b"<code>http://host:8787/?token=YOUR_TOKEN</code> once and the token is "
              b"remembered in a cookie.</p><p>The token is <code>server.token</code> in "
              b"<code>config.toml</code> on the monitored machine.</p>")

    def _json(self, payload: dict, code: int = 200) -> None:
        self._send(code, json.dumps(payload, allow_nan=False, default=str).encode(),
                   "application/json; charset=utf-8")

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        route = urlparse(self.path)
        path = route.path.rstrip("/") or "/"
        try:
            allowed, from_query = self._check_token(route)
            if not allowed:
                if path.startswith("/api") or path == "/healthz":
                    self._json({"error": "unauthorised: token required"}, 401)
                else:
                    self._send(401, self.DENIED, "text/html; charset=utf-8")
                return
            if path == "/":
                page = HERE / "dashboard.html"
                # Remember a token passed in the URL, so the link works once and
                # the token stops appearing in the address bar afterwards.
                cookie = []
                if from_query:
                    cookie = [("Set-Cookie",
                               f"powermon_token={self.cfg['server']['token']}; Max-Age=31536000; "
                               "Path=/; SameSite=Lax; HttpOnly")]
                self._send(200, page.read_bytes(), "text/html; charset=utf-8", extra=cookie)
            elif path == "/api/now":
                self._json(now_payload(self.monitor, self.store, self.cfg))
            elif path == "/api/history":
                span = (parse_qs(route.query).get("range") or ["24h"])[0]
                if span not in ("24h", "7d", "30d", "90d"):
                    span = "24h"
                self._json({"range": span, "series": history(self.store, self.monitor, span)})
            elif path == "/healthz":
                age = time.time() - (self.monitor.latest.get("ts") or 0)
                self._json({"ok": age < 30, "last_sample_age_s": age})
            else:
                self._json({"error": "not found"}, 404)
        except BrokenPipeError:
            pass
        except Exception as exc:
            print(f"powermon: request {self.path} failed: {exc!r}", file=sys.stderr, flush=True)
            try:
                self._json({"error": repr(exc)}, 500)
            except OSError:
                pass


def main() -> int:
    cfg_path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "config.toml"
    cfg = load_config(cfg_path)
    db_path = Path(os.path.expanduser(
        str(cfg["sampling"].get("db") or HERE / "powermon.db")))
    store = Store(db_path)
    monitor = Monitor(cfg, store)

    monitor.sample_once()  # prime deltas so the first HTTP hit has real data
    threading.Thread(target=monitor.run, name="sampler", daemon=True).start()

    Handler.monitor, Handler.store, Handler.cfg = monitor, store, cfg
    host, port = cfg["server"]["host"], int(cfg["server"]["port"])
    httpd = ThreadingHTTPServer((host, port), Handler)
    src = "RAPL (measured)" if monitor.cpu.source == "rapl" else "utilisation model (estimated)"
    print(f"powermon: http://{host}:{port}  db={db_path}", flush=True)
    print(f"powermon: CPU power from {src}; GPU: {monitor.gpu.name or 'none detected'}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        monitor.stop()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
