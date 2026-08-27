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

import ctypes
import hashlib
import hmac
import ipaddress
import secrets
import ssl
import json
import os
import re
import shutil
import socket
import urllib.request
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
DEFAULTS = {
    "server": {"host": "127.0.0.1", "port": 8787, "token": "",
               "trusted_proxies": "", "require_token_always": False,
               "keys_file": "", "tls_cert": "", "tls_key": ""},
    "sampling": {"interval": 2.0, "raw_retention_days": 7, "gap_max_s": 60.0},
    "tariff": {"currency": "EUR", "symbol": "EUR", "mode": "flat",
               "rate": 0.15, "standing_charge_per_day": 0.0},
    "power": {"psu_efficiency": 0.90, "baseline_w": 35.0},
    "cpu": {"rapl_scale": 1.0, "idle_w": 30.0, "max_w": 142.0, "curve_exp": 1.25},
    "gpu": {"enabled": True},
    "meter": {"type": "none", "timeout": 1.0,
              "http_url": "", "http_json_path": "", "http_headers": "", "http_scale": 1.0,
              "nut_host": "127.0.0.1", "nut_port": 3493, "nut_ups": "ups",
              "nut_var": "ups.realpower"},
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


# --------------------------------------------------------------------------- access control
#
# One rule decides access: resolve the *effective* client address first, then
# judge that. Source address alone is not evidence of locality -- a reverse
# proxy on this host makes every request look like 127.0.0.1 -- so forwarding
# headers are believed only from proxies the operator has explicitly trusted.


def _ip(addr: str):
    """Parse an address, unwrapping IPv4-mapped IPv6 so ::ffff:127.0.0.1 is loopback."""
    try:
        parsed = ipaddress.ip_address(str(addr).strip())
    except (ValueError, AttributeError):
        return None
    return getattr(parsed, "ipv4_mapped", None) or parsed


def is_loopback(addr: str) -> bool:
    parsed = _ip(addr)
    return bool(parsed and parsed.is_loopback)


def parse_trusted_proxies(value) -> list:
    """Addresses or CIDR ranges, as a comma-separated string or a list.

    A string rather than a TOML array because the interpreter on the target
    host predates tomllib, and the fallback parser cannot read arrays.
    Raises ValueError on anything unparseable: silently dropping an entry
    here would silently change who is trusted.
    """
    if not value:
        return []
    items = value if isinstance(value, (list, tuple)) else str(value).split(",")
    nets = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        try:
            nets.append(ipaddress.ip_network(text, strict=False))
        except ValueError:
            raise ValueError(f"{text!r} is not an IP address or CIDR range") from None
    return nets


def in_networks(addr: str, nets: list) -> bool:
    parsed = _ip(addr)
    if parsed is None:
        return False
    for net in nets:
        try:
            if parsed in net:
                return True
        except TypeError:  # comparing IPv4 against an IPv6 network, and vice versa
            continue
    return False


def effective_client(peer: str, forwarded_for: str | None, trusted: list) -> str:
    """The address authorisation should judge.

    When the peer is not a trusted proxy its headers are ignored entirely, so
    an attacker cannot claim to be loopback. When it is, the client is the
    right-most address in the chain that is not itself a trusted proxy.
    Trusting a proxy means trusting it to send X-Forwarded-For.
    """
    if not trusted or not in_networks(peer, trusted):
        return peer
    chain = [part.strip() for part in (forwarded_for or "").split(",") if part.strip()]
    for addr in reversed(chain):
        if not in_networks(addr, trusted):
            return addr
    return chain[0] if chain else peer


SCOPES = ("read", "admin")


def hash_token(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode("utf-8", "replace")).hexdigest()


def load_keys(cfg: dict, base_dir: Path | None = None) -> list[dict]:
    """Every credential that may open this server.

    `server.token` stays supported as a one-key shorthand, because most
    installs have exactly one client and should not need a key file to say so.
    Additional named keys live in a JSON file so they can be revoked one at a
    time, and are stored hashed: a leaked config should not be a leaked
    credential.
    """
    server = cfg.get("server", {})
    keys: list[dict] = []

    token = str(server.get("token") or "")
    if token:
        keys.append({"name": "config-token", "scope": "admin",
                     "hash": hash_token(token), "created": None})

    path = str(server.get("keys_file") or "")
    if path:
        full = Path(os.path.expanduser(path))
        if not full.is_absolute():
            full = (base_dir or HERE) / full
        try:
            entries = json.loads(full.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return keys
        for entry in entries if isinstance(entries, list) else []:
            digest = str(entry.get("hash") or "")
            if not digest:
                continue
            scope = str(entry.get("scope") or "read")
            keys.append({"name": str(entry.get("name") or "unnamed"),
                         "scope": scope if scope in SCOPES else "read",
                         "hash": digest,
                         "created": entry.get("created")})
    return keys


def match_key(supplied: str | None, keys: list[dict]) -> dict | None:
    """The key this token belongs to, or None.

    Every candidate is compared even after a match, so the time taken does not
    reveal how far down the list the right key sits.
    """
    if not supplied or not keys:
        return None
    digest = hash_token(supplied)
    found = None
    for key in keys:
        # compare_digest on the hashes: equal length, and no timing signal from
        # the token itself.
        if hmac.compare_digest(digest, str(key["hash"])) and found is None:
            found = key
    return found


def authorize(*, client: str, token_supplied: str | None, keys: list[dict],
              require_token_always: bool = False,
              required_scope: str = "read") -> tuple[bool, dict | None]:
    """Returns (allowed, the key that opened it).

    Loopback is exempt so `pwr` and SSH tunnels need no configuration. Every
    other client must present a key. No keys configured denies remote access
    rather than granting it: no credential means no way in, not a way in for
    everyone.
    """
    if is_loopback(client) and not require_token_always:
        return True, None
    key = match_key(token_supplied, keys)
    if key is None:
        return False, None
    # admin implies read. No route requires admin yet -- every endpoint is
    # read-only -- so this gate exists for the first one that does.
    if required_scope == "admin" and key["scope"] != "admin":
        return False, key
    return True, key


class Throttle:
    """Delays repeated authentication failures from one address.

    An 18-byte token is not guessable, but nothing stopped a client trying at
    line speed, and a shorter hand-picked one might be. Successful requests are
    never delayed, so this cannot lock out a client that has the right key.
    """

    def __init__(self, threshold: int = 5, base_delay: float = 1.0,
                 max_delay: float = 30.0):
        self.threshold = threshold
        self.base_delay = base_delay
        self.max_delay = max_delay
        self._fails: dict[str, int] = {}
        self._lock = threading.Lock()

    def delay_for(self, client: str) -> float:
        """Seconds this client should wait before its next attempt is answered."""
        with self._lock:
            fails = self._fails.get(client, 0)
        if fails < self.threshold:
            return 0.0
        # Doubling, so a serious attempt slows to a crawl while a fat-fingered
        # token costs a second.
        return min(self.max_delay, self.base_delay * 2 ** (fails - self.threshold))

    def record_failure(self, client: str) -> None:
        with self._lock:
            # Bounded, so a spray of forged source addresses cannot grow this
            # without limit.
            if len(self._fails) > 4096:
                self._fails.clear()
            self._fails[client] = self._fails.get(client, 0) + 1

    def record_success(self, client: str) -> None:
        with self._lock:
            self._fails.pop(client, None)


def validate_server_config(cfg: dict, cfg_path: Path | None = None) -> str | None:
    """Returns a fatal error message, or None when the configuration is safe."""
    server = cfg.get("server", {})
    host = str(server.get("host") or "")
    try:
        parse_trusted_proxies(server.get("trusted_proxies"))
    except ValueError as exc:
        return f"server.trusted_proxies: {exc}"

    cert, key = str(server.get("tls_cert") or ""), str(server.get("tls_key") or "")
    if bool(cert) != bool(key):
        return ("server.tls_cert and server.tls_key must be set together; "
                f"only {'tls_cert' if cert else 'tls_key'} is set.")
    for label, path in (("tls_cert", cert), ("tls_key", key)):
        if path and not Path(os.path.expanduser(path)).exists():
            return f"server.{label}: {path} does not exist."

    if load_keys(cfg, cfg_path.parent if cfg_path else None) or is_loopback(host):
        return None
    where = f" in {cfg_path}" if cfg_path else ""
    return (
        f"refusing to start: server.host is {host!r}, which accepts connections from "
        f"other machines, but no access key is configured{where}.\n"
        "  Anyone able to reach the port would have full access to your data.\n"
        "  Generate a token with:  python3 -c \"import secrets; "
        "print(secrets.token_urlsafe(18))\"\n"
        "  or add a named key with:  ./powermon.py --add-key NAME\n"
        "  Or set host = \"127.0.0.1\" and reach it through an SSH tunnel."
    )


# --------------------------------------------------------------------------- readers


class CpuReader:
    """CPU utilisation, temperature and power.

    Power comes from the RAPL package energy counter if it is readable; the
    fallback is a utilisation curve between idle_w and max_w.
    """

    RAPL_ROOT = Path("/sys/class/powercap")

    def __init__(self, cfg: dict, rapl_root: Path | None = None):
        self.cfg = cfg
        self._stat: tuple[int, int] | None = None
        # One entry per CPU package: {"energy": Path, "max": int, "prev": (ts, uj)}
        self._rapl: list[dict] = []
        self.source = "estimated"
        self._find_rapl(rapl_root or self.RAPL_ROOT)
        self._temp_path = self._find_cpu_temp()
        self.ncpu = os.cpu_count() or 1

    def _find_rapl(self, root: Path) -> None:
        for dom in sorted(root.glob("*-rapl:[0-9]")):
            name_file, energy = dom / "name", dom / "energy_uj"
            try:
                if not name_file.read_text().strip().startswith("package"):
                    continue
                energy.read_text()  # permission probe
            except OSError:
                continue
            try:
                wrap = int((dom / "max_energy_range_uj").read_text())
            except OSError:
                wrap = 2**32
            # Every package, not just the first. A two-socket board exposes one
            # domain per socket, and stopping at the first halves the CPU figure
            # on exactly the machines most likely to have two.
            self._rapl.append({"energy": energy, "max": wrap, "prev": None})
        if self._rapl:
            self.source = "rapl"

    @property
    def packages(self) -> int:
        return len(self._rapl)

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
        if not self._rapl:
            return self._estimate(util_pct)
        total = 0.0
        complete = True
        for dom in self._rapl:
            try:
                energy = int(dom["energy"].read_text())
            except OSError:
                # The counters went away (permissions, hotplug): fall back for
                # good rather than reporting a partial socket as the whole CPU.
                self._rapl, self.source = [], "estimated"
                return self._estimate(util_pct)
            prev, dom["prev"] = dom["prev"], (now, energy)
            if prev is None:
                complete = False
                continue
            dt = now - prev[0]
            d_uj = energy - prev[1]
            if d_uj < 0:  # counter wrapped
                d_uj += dom["max"]
            if dt <= 0:
                complete = False
                continue
            total += (d_uj / 1e6) / dt
        if not complete:
            return self._estimate(util_pct)
        watts = total * float(self.cfg["cpu"]["rapl_scale"])
        return watts if 0 <= watts < 1000 else self._estimate(util_pct)

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


def aggregate_gpus(devices: list[dict]) -> dict:
    """Collapse per-device readings into the host-level view.

    Power is the sum across every device, and is unknown when ANY device's
    power is unknown: a host total that quietly drops a card is the
    multi-device form of the false zero, and it understates by whole
    hundreds of watts rather than a rounding error.

    Memory and limits sum over whatever is known. Temperature, fan and clock
    take the peak, since the question they answer is "is anything hot".
    """
    if not devices:
        return {"power_w": None, "util": None, "mem_used": None, "mem_total": None,
                "temp": None, "fan": None, "clock_mhz": None, "limit_w": None,
                "name": None, "count": 0}

    powers = [d.get("power_w") for d in devices]
    power = None if any(p is None for p in powers) else sum(powers)

    def total(key):
        known = [d[key] for d in devices if d.get(key) is not None]
        return sum(known) if known else None

    def peak(key):
        known = [d[key] for d in devices if d.get(key) is not None]
        return max(known) if known else None

    names = [d.get("name") for d in devices if d.get("name")]
    if len(devices) == 1:
        name = names[0] if names else None
    elif names and len(set(names)) == 1:
        name = f"{len(devices)} x {names[0]}"
    else:
        name = f"{len(devices)} GPUs"

    return {"power_w": power, "util": peak("util"), "mem_used": total("mem_used"),
            "mem_total": total("mem_total"), "temp": peak("temp"), "fan": peak("fan"),
            "clock_mhz": peak("clock_mhz"), "limit_w": total("limit_w"),
            "name": name, "count": len(devices)}


class Nvml:
    """The driver's own C library, called directly through ctypes.

    Forking nvidia-smi twice per sample cost about 40 ms of CPU, some 80 % of
    the daemon's total, because process creation dwarfs the query itself. The
    same numbers come from libnvidia-ml.so for about 1 ms, and ctypes is
    standard library, so this costs no dependency.

    Every accessor returns None rather than raising or substituting a zero: an
    unreadable sensor is an unknown, and the ledger distinguishes the two.
    """

    SUCCESS = 0
    INSUFFICIENT_SIZE = 7
    TEMPERATURE_GPU = 0
    CLOCK_SM = 1

    class _Util(ctypes.Structure):
        _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

    class _Mem(ctypes.Structure):
        _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong),
                    ("used", ctypes.c_ulonglong)]

    class _Proc(ctypes.Structure):
        # v3 layout; the compiler pads after pid to align the 64-bit member.
        _fields_ = [("pid", ctypes.c_uint), ("used_mem", ctypes.c_ulonglong),
                    ("gpu_instance", ctypes.c_uint), ("compute_instance", ctypes.c_uint)]

    def __init__(self):
        self.lib = None
        self.handles: list = []
        self._procs_fn = None
        try:
            lib = ctypes.CDLL("libnvidia-ml.so.1")
        except OSError:
            return
        if lib.nvmlInit_v2() != self.SUCCESS:
            return
        count = ctypes.c_uint()
        if lib.nvmlDeviceGetCount_v2(ctypes.byref(count)) != self.SUCCESS:
            lib.nvmlShutdown()
            return
        handles = []
        for index in range(count.value):
            handle = ctypes.c_void_p()
            if lib.nvmlDeviceGetHandleByIndex_v2(index, ctypes.byref(handle)) == self.SUCCESS:
                handles.append((index, handle))
        if not handles:
            lib.nvmlShutdown()
            return
        self.lib, self.handles = lib, handles
        # Driver-dependent name; older drivers expose v2 or an unsuffixed symbol.
        for suffix in ("_v3", "_v2", ""):
            self._procs_fn = getattr(lib, f"nvmlDeviceGetComputeRunningProcesses{suffix}", None)
            if self._procs_fn is not None:
                break

    @property
    def available(self) -> bool:
        return self.lib is not None

    def shutdown(self) -> None:
        if self.lib is not None:
            try:
                self.lib.nvmlShutdown()
            except OSError:
                pass
            self.lib, self.handles = None, []

    def _uint(self, fn_name, handle, *extra) -> float | None:
        fn = getattr(self.lib, fn_name, None)
        if fn is None:
            return None
        out = ctypes.c_uint()
        if fn(handle, *extra, ctypes.byref(out)) != self.SUCCESS:
            return None
        return float(out.value)

    def identify(self) -> list[dict]:
        out = []
        for index, handle in self.handles:
            buf = ctypes.create_string_buffer(96)
            name = None
            if self.lib.nvmlDeviceGetName(handle, buf, 96) == self.SUCCESS:
                name = buf.value.decode("utf-8", "replace")
            limit_mw = self._uint("nvmlDeviceGetEnforcedPowerLimit", handle)
            out.append({"index": index, "name": name,
                        "limit_w": limit_mw / 1000.0 if limit_mw else 350.0})
        return out

    def sample(self) -> list[dict] | None:
        """Per-device readings, or None if the library has gone away."""
        if not self.available:
            return None
        devices = []
        for index, handle in self.handles:
            power_mw = self._uint("nvmlDeviceGetPowerUsage", handle)
            util = self._Util()
            if self.lib.nvmlDeviceGetUtilizationRates(handle, ctypes.byref(util)) == self.SUCCESS:
                util_pct = float(util.gpu)
            else:
                util_pct = None
            mem = self._Mem()
            if self.lib.nvmlDeviceGetMemoryInfo(handle, ctypes.byref(mem)) == self.SUCCESS:
                mem_used, mem_total = mem.used / 1048576.0, mem.total / 1048576.0
            else:
                mem_used = mem_total = None
            devices.append({
                "index": index,
                "power_w": power_mw / 1000.0 if power_mw is not None else None,
                "util": util_pct,
                "mem_used": mem_used,
                "mem_total": mem_total,
                "temp": self._uint("nvmlDeviceGetTemperature", handle, self.TEMPERATURE_GPU),
                "fan": self._uint("nvmlDeviceGetFanSpeed", handle),
                "clock_mhz": self._uint("nvmlDeviceGetClockInfo", handle, self.CLOCK_SM),
            })
        return devices

    def processes(self, limit: int = 6) -> list[dict]:
        if not self.available or self._procs_fn is None:
            return []
        out = []
        for _, handle in self.handles:
            count = ctypes.c_uint(0)
            # Count first: the call reports INSUFFICIENT_SIZE and fills in count.
            rc = self._procs_fn(handle, ctypes.byref(count), None)
            if rc not in (self.SUCCESS, self.INSUFFICIENT_SIZE) or count.value == 0:
                continue
            arr = (self._Proc * count.value)()
            if self._procs_fn(handle, ctypes.byref(count), arr) != self.SUCCESS:
                continue
            for proc in arr[:count.value]:
                buf = ctypes.create_string_buffer(256)
                name = str(proc.pid)
                if self.lib.nvmlSystemGetProcessName(proc.pid, buf, 256) == self.SUCCESS:
                    # Full path, where nvidia-smi reported a basename.
                    name = Path(buf.value.decode("utf-8", "replace")).name[:40]
                out.append({"pid": str(proc.pid), "name": name,
                            "mem_mib": proc.used_mem / 1048576.0})
        return out[:limit]


class GpuReader:
    """NVIDIA GPU sampling across every installed device.

    Prefers NVML through ctypes; falls back to parsing nvidia-smi when the
    library cannot be loaded, which keeps behaviour identical on hosts where
    only the binary is present.
    """

    FIELDS = ("index", "power.draw", "utilization.gpu", "memory.used", "memory.total",
              "temperature.gpu", "fan.speed", "clocks.sm")

    def __init__(self, cfg: dict, nvml: "Nvml | None" = None):
        want_gpu = bool(cfg["gpu"]["enabled"])
        self.nvml = nvml if nvml is not None else (Nvml() if want_gpu else None)
        if self.nvml is not None and not self.nvml.available:
            self.nvml = None
        # NVML alone is enough; nvidia-smi is only needed for the fallback path.
        self.enabled = want_gpu and (self.nvml is not None
                                     or shutil.which("nvidia-smi") is not None)
        self.name = None
        self.power_limit = 350.0
        self.devices: list[dict] = []      # identity per device, by index
        self.fail_count = 0
        self._identify_countdown = 0
        if self.enabled:
            self._identify()

    @property
    def source(self) -> str:
        return "nvml" if self.nvml is not None else "nvidia-smi"

    def close(self) -> None:
        if self.nvml is not None:
            self.nvml.shutdown()

    def _identify(self) -> bool:
        """Read each card's name and power limit. Retried until it succeeds: with
        lingering enabled the service starts at boot, which can be before the
        NVIDIA driver is ready -- a one-shot query there would leave the name
        blank and the power limit stuck on the fallback for the whole uptime."""
        if self.nvml is not None:
            devices = [d for d in self.nvml.identify() if d.get("name")]
            if not devices:
                return False
            self.devices = devices
            self.name = devices[0]["name"]
            self.power_limit = sum(d["limit_w"] for d in devices)
            return True
        info = self._run(["--query-gpu=index,name,enforced.power.limit",
                          "--format=csv,noheader,nounits"])
        if not info:
            return False
        devices = []
        for row in info:
            parts = [p.strip() for p in row.split(", ")]
            if len(parts) < 2:
                continue
            index = _num(parts[0])
            limit = _num(parts[2]) if len(parts) > 2 else None
            devices.append({"index": int(index) if index is not None else len(devices),
                            "name": parts[1],
                            "limit_w": limit if limit else 350.0})
        if not devices:
            return False
        self.devices = devices
        self.name = devices[0]["name"]
        self.power_limit = sum(d["limit_w"] for d in devices)
        return True

    @staticmethod
    def _run(args: list[str]) -> list[str] | None:
        try:
            out = subprocess.run(["nvidia-smi", *args], capture_output=True, text=True,
                                 timeout=10, check=True).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        return [ln for ln in (l.strip() for l in out.splitlines()) if ln]

    def _sample_nvml(self) -> dict:
        readings = self.nvml.sample()
        if not readings:
            self.fail_count += 1
            return {"present": True, "error": True}
        self.fail_count = 0
        if self.name is None:
            # Same throttled retry as the subprocess path: the driver may not
            # have been ready when the service started at boot.
            if self._identify_countdown <= 0:
                self._identify_countdown = 0 if self._identify() else 30
            else:
                self._identify_countdown -= 1
        devices = []
        for reading in readings:
            meta = next((d for d in self.devices if d["index"] == reading["index"]), None)
            device = dict(reading)
            device["name"] = meta["name"] if meta else self.name
            device["limit_w"] = meta["limit_w"] if meta else self.power_limit
            devices.append(device)
        sample = aggregate_gpus(devices)
        sample.update({"present": True, "procs": self.nvml.processes(), "devices": devices})
        return sample

    def sample(self) -> dict:
        """Never raises: a driver hiccup yields None values, not a dead sampler."""
        if not self.enabled:
            return {"present": False}
        if self.nvml is not None:
            return self._sample_nvml()
        rows = self._run([f"--query-gpu={','.join(self.FIELDS)}", "--format=csv,noheader,nounits"])
        if not rows:
            self.fail_count += 1
            return {"present": True, "error": True}
        self.fail_count = 0
        # The driver is clearly up now; fill in identity if the boot-time query lost
        # the race. Throttled so a card that genuinely reports no name costs nothing.
        if self.name is None:
            if self._identify_countdown <= 0:
                self._identify_countdown = 0 if self._identify() else 30
            else:
                self._identify_countdown -= 1
        devices = []
        for row in rows:
            vals = dict(zip(self.FIELDS, [_num(item) for item in row.split(", ")]))
            index = vals.get("index")
            index = int(index) if index is not None else len(devices)
            meta = next((d for d in self.devices if d["index"] == index), None)
            devices.append({
                "index": index,
                "name": meta["name"] if meta else self.name,
                "power_w": vals.get("power.draw"),
                "util": vals.get("utilization.gpu"),
                "mem_used": vals.get("memory.used"),
                "mem_total": vals.get("memory.total"),
                "temp": vals.get("temperature.gpu"),
                "fan": vals.get("fan.speed"),
                "clock_mhz": vals.get("clocks.sm"),
                "limit_w": meta["limit_w"] if meta else self.power_limit,
            })
        procs = []
        app_rows = self._run(["--query-compute-apps=pid,process_name,used_memory",
                              "--format=csv,noheader,nounits"]) or []
        for row in app_rows[:6]:
            parts = row.split(", ")
            if len(parts) >= 3:
                procs.append({"pid": parts[0],
                              "name": Path(parts[1]).name[:40],
                              "mem_mib": _num(parts[2])})
        sample = aggregate_gpus(devices)
        sample.update({"present": True, "procs": procs, "devices": devices})
        return sample


def _num(text: str) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- wall meters
#
# A real meter turns the headline number from an estimate into a measurement,
# and demotes baseline_w and psu_efficiency from assumptions to a residual that
# can be reported. Homelabs usually already own one: a UPS, a smart plug, or a
# metered PDU.
#
# Every provider returns watts or None. None means "could not read", never zero,
# and the caller falls back to the model rather than inventing a number.


def dig(data, path: str):
    """Value at a dotted path: "a.b.0.c". Returns None if any step is missing."""
    if not path:
        return data
    current = data
    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        elif isinstance(current, (list, tuple)):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


class HttpMeter:
    """Any device that reports power as JSON over HTTP.

    One provider covers the three things a homelab is likely to have, because
    they differ only in URL and where the number sits:

      Tasmota     /cm?cmnd=Status%2010      StatusSNS.ENERGY.Power
      Shelly gen1 /status                   meters.0.power
      Shelly gen2 /rpc/Switch.GetStatus?id=0  apower
      Home Asst.  /api/states/sensor.x      state   (plus a bearer header)
    """

    def __init__(self, url: str, json_path: str, headers: str = "",
                 scale: float = 1.0, timeout: float = 1.0, opener=None):
        self.url = url
        self.json_path = json_path
        self.scale = scale
        self.timeout = timeout
        self.headers = {}
        for part in (headers or "").split("|"):
            name, sep, value = part.partition(":")
            if sep and name.strip():
                self.headers[name.strip()] = value.strip()
        self._opener = opener or urllib.request.urlopen

    def read(self) -> float | None:
        request = urllib.request.Request(self.url, headers=self.headers)
        try:
            with self._opener(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
        except Exception:
            # Any transport or parse failure is an unknown reading, not a fault
            # worth killing the sampler over.
            return None
        # Home Assistant reports "123.4" as a string, hence _num rather than float.
        watts = _num(dig(payload, self.json_path))
        return watts * self.scale if watts is not None else None


class NutMeter:
    """Network UPS Tools, the most common meter in a homelab with a rack.

    Prefers ups.realpower. Most consumer UPSs do not report it, so the fallback
    is load percentage against the nominal rating -- cruder, but still a
    measurement of the whole load rather than a guess about this machine.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 3493, ups: str = "ups",
                 var: str = "ups.realpower", timeout: float = 1.0, connect=None):
        self.host, self.port, self.ups, self.var = host, port, ups, var
        self.timeout = timeout
        self._connect = connect or self._tcp

    def _tcp(self):
        return socket.create_connection((self.host, self.port), timeout=self.timeout)

    @staticmethod
    def _parse(line: str) -> float | None:
        # VAR ups ups.realpower "123.4"
        if not line.startswith("VAR "):
            return None
        _, _, rest = line.partition('"')
        return _num(rest.rpartition('"')[0]) if '"' in line else None

    def read(self) -> float | None:
        try:
            with self._connect() as sock:
                sock.settimeout(self.timeout)
                reader = sock.makefile("rw", encoding="utf-8", newline="\n")

                def ask(var: str) -> float | None:
                    reader.write(f"GET VAR {self.ups} {var}\n")
                    reader.flush()
                    return self._parse(reader.readline().strip())

                watts = ask(self.var)
                if watts is None:
                    # Fall back to load% x nominal rating.
                    load = ask("ups.load")
                    nominal = ask("ups.realpower.nominal")
                    if load is not None and nominal is not None:
                        watts = load / 100.0 * nominal
                reader.write("LOGOUT\n")
                reader.flush()
                return watts
        except Exception:
            return None


def build_meter(cfg: dict):
    """The configured meter, or None when there is none."""
    meter = cfg.get("meter", {})
    kind = str(meter.get("type") or "none").strip().lower()
    timeout = float(meter.get("timeout") or 1.0)
    if kind in ("", "none"):
        return None
    if kind == "http":
        url = str(meter.get("http_url") or "")
        if not url:
            return None
        return HttpMeter(url, str(meter.get("http_json_path") or ""),
                         str(meter.get("http_headers") or ""),
                         float(meter.get("http_scale") or 1.0), timeout)
    if kind == "nut":
        return NutMeter(str(meter.get("nut_host") or "127.0.0.1"),
                        int(float(meter.get("nut_port") or 3493)),
                        str(meter.get("nut_ups") or "ups"),
                        str(meter.get("nut_var") or "ups.realpower"), timeout)
    return None


class MeterPoller:
    """Wraps a provider so a slow or dead meter cannot stall the sampler.

    After repeated failures it is tried less often: a meter that is switched
    off should not cost a timeout on every sample.
    """

    def __init__(self, meter, max_failures: int = 3, retry_every: int = 30):
        self.meter = meter
        self.max_failures = max_failures
        self.retry_every = retry_every
        self.failures = 0
        self._skip = 0
        self.last_error = False

    def read(self) -> float | None:
        if self.meter is None:
            return None
        if self._skip > 0:
            self._skip -= 1
            return None
        watts = self.meter.read()
        if watts is None or watts < 0:
            self.failures += 1
            self.last_error = True
            if self.failures >= self.max_failures:
                self._skip = self.retry_every
            return None
        self.failures = 0
        self.last_error = False
        return watts


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
  cost REAL, rate REAL, secs REAL, max_w REAL,
  -- Seconds this hour that were NOT integrated because a meter was
  -- unavailable. secs and secs_missing together are the elapsed time.
  secs_missing REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS days (
  -- Local midnight, epoch seconds. One row per day we actually recorded on,
  -- holding the standing charge in effect that day. Written only for the
  -- current day, so a later tariff change cannot rewrite a closed one.
  day INTEGER PRIMARY KEY,
  standing REAL NOT NULL DEFAULT 0
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
            self._migrate()
            self.db.execute("PRAGMA journal_mode=WAL")
            # ~43k commits/day: NORMAL skips an fsync per commit, risking only the
            # last few seconds of samples on a power cut. Right trade for telemetry.
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.execute("PRAGMA wal_autocheckpoint=256")
            self.db.commit()

    def _migrate(self) -> None:
        """Add columns that CREATE TABLE IF NOT EXISTS will not add to an old file."""
        cols = {row["name"] for row in self.db.execute("PRAGMA table_info(hourly)")}
        if "secs_missing" not in cols:
            # Hours recorded before coverage tracking are left at 0 missing:
            # that is what they were already assumed to be, and inventing a
            # different figure retroactively would be its own falsehood.
            self.db.execute(
                "ALTER TABLE hourly ADD COLUMN secs_missing REAL NOT NULL DEFAULT 0")

    def add_sample(self, s: dict) -> None:
        with self.lock:
            self.db.execute(
                "INSERT OR REPLACE INTO samples VALUES "
                "(:ts,:total_w,:cpu_w,:gpu_w,:other_w,:cpu_pct,:ram_pct,"
                ":gpu_util,:gpu_mem_pct,:cpu_temp,:gpu_temp,:busy)", s)
            self.db.commit()

    def add_energy(self, t0: float, t1: float, watts: dict, busy: bool, rate: float,
                   complete: bool = True) -> None:
        """Integrate watts over [t0, t1), splitting across hour boundaries.

        An incomplete interval -- one where a meter could not be read -- adds no
        energy, no cost and no peak, and is recorded as missing time instead.
        Substituting zero would quietly lower the recorded consumption, and a
        silently cheaper month is worse than a visible gap.
        """
        with self.lock:
            while t0 < t1:
                hour = int(t0 // 3600) * 3600
                seg_end = min(t1, hour + 3600)
                dt = seg_end - t0
                factor = dt / 3600.0 if complete else 0.0
                wh = watts["total"] * factor
                self.db.execute(
                    "INSERT INTO hourly (hour, wh, wh_busy, wh_idle, gpu_wh, cpu_wh, other_wh,"
                    " cost, rate, secs, max_w, secs_missing) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(hour) DO UPDATE SET "
                    " wh=wh+excluded.wh, wh_busy=wh_busy+excluded.wh_busy,"
                    " wh_idle=wh_idle+excluded.wh_idle, gpu_wh=gpu_wh+excluded.gpu_wh,"
                    " cpu_wh=cpu_wh+excluded.cpu_wh, other_wh=other_wh+excluded.other_wh,"
                    " cost=cost+excluded.cost, rate=excluded.rate, secs=secs+excluded.secs,"
                    " max_w=MAX(max_w, excluded.max_w),"
                    " secs_missing=secs_missing+excluded.secs_missing",
                    (hour, wh, wh if busy else 0.0, 0.0 if busy else wh,
                     watts["gpu"] * factor, watts["cpu"] * factor, watts["other"] * factor,
                     wh / 1000.0 * rate, rate,
                     dt if complete else 0.0,
                     watts["total"] if complete else 0.0,
                     0.0 if complete else dt))
                t0 = seg_end
            self.db.commit()

    def note_day(self, day_start: float, standing: float) -> None:
        """Record the standing charge in effect for a day being sampled.

        Called only for the current day. Yesterday's row is never revisited, so
        editing the tariff cannot change what a closed day already cost.
        """
        with self.lock:
            self.db.execute(
                "INSERT INTO days (day, standing) VALUES (?,?) "
                "ON CONFLICT(day) DO UPDATE SET standing=excluded.standing",
                (int(day_start), float(standing)))
            self.db.commit()

    def prune(self, older_than_ts: float) -> None:
        with self.lock:
            self.db.execute("DELETE FROM samples WHERE ts < ?", (older_than_ts,))
            self.db.commit()

    def query(self, sql: str, args: tuple | dict = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute(sql, args).fetchall()


# --------------------------------------------------------------------------- sampler


class Monitor:
    def __init__(self, cfg: dict, store: Store):
        self.cfg = cfg
        self.store = store
        self.cpu = CpuReader(cfg)
        self.gpu = GpuReader(cfg)
        self.meter = MeterPoller(build_meter(cfg))
        self.net = NetReader()
        self.latest: dict = {}
        self.recent: list[dict] = []       # in-memory ring for sparklines
        self.started = time.time()
        self.session_wh = 0.0
        self._prev_ts: float | None = None
        self._noted_day: tuple[float, float] | None = None
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
        raw_gpu_w = gpu.get("power_w")
        # A machine with no GPU contributes a known zero. A GPU whose sensor
        # failed contributes an unknown, which is a different thing, and the
        # difference is the whole point: `or 0.0` used to erase it.
        gpu_known = (not gpu.get("present")) or raw_gpu_w is not None
        gpu_w = float(raw_gpu_w) if raw_gpu_w is not None else 0.0
        eff = max(0.5, min(1.0, float(self.cfg["power"]["psu_efficiency"])))
        dc = cpu_w + gpu_w + float(self.cfg["power"]["baseline_w"])
        modelled_w = dc / eff

        # A meter measures the wall directly, so it wins. The model is then only
        # deciding how to attribute that total between components, and the gap
        # between the two is a calibration residual worth reporting rather than
        # an error worth hiding.
        meter_w = self.meter.read()
        wall_source = "meter" if meter_w is not None else "model"
        total_w = meter_w if meter_w is not None else modelled_w
        residual_w = (meter_w - modelled_w) if meter_w is not None else None
        other_w = max(0.0, total_w - cpu_w - gpu_w)

        act = self.cfg["activity"]
        busy = bool((gpu.get("util") or 0) >= float(act["busy_gpu_util"])
                    or gpu_w >= float(act["busy_gpu_power_w"]))
        mem = read_mem()
        mem_total = gpu.get("mem_total") or 0.0
        gpu_mem_pct = 100.0 * (gpu.get("mem_used") or 0.0) / mem_total if mem_total else 0.0

        # The stored record keeps unknowns as NULL, so a failed read shows as a
        # gap in the history rather than a dip to zero.
        row = {"ts": now,
               "total_w": total_w if gpu_known else None,
               "cpu_w": cpu_w,
               "gpu_w": gpu_w if gpu_known else None,
               "other_w": other_w if gpu_known else None,
               "cpu_pct": util, "ram_pct": mem["pct"],
               "gpu_util": gpu.get("util") or 0.0, "gpu_mem_pct": gpu_mem_pct,
               "cpu_temp": self.cpu.temp(), "gpu_temp": gpu.get("temp"),
               "busy": int(busy)}
        self.store.add_sample(row)

        # Stamp today with the standing charge now in effect. Once per day, or
        # again if the tariff is edited while the day is still open.
        standing = float(self.cfg["tariff"].get("standing_charge_per_day", 0.0) or 0.0)
        day = _day_start(datetime.fromtimestamp(now))
        if self._noted_day != (day, standing):
            self.store.note_day(day, standing)
            self._noted_day = (day, standing)

        # energy integration, skipping service-downtime gaps
        gap_max = float(self.cfg["sampling"]["gap_max_s"])
        if self._prev_ts is not None and 0 < now - self._prev_ts <= gap_max:
            watts = {"total": total_w, "cpu": cpu_w, "gpu": gpu_w, "other": other_w}
            self.store.add_energy(self._prev_ts, now, watts, busy, self.rate,
                                  complete=gpu_known)
            if gpu_known:
                self.session_wh += total_w * (now - self._prev_ts) / 3600.0
        self._prev_ts = now

        # The live view keeps numbers so the API shape is unchanged; `partial`
        # and the existing gpu.error flag are how a client knows the total is
        # missing its GPU term.
        detail = dict(row)
        detail.update({"total_w": total_w, "gpu_w": gpu_w, "other_w": other_w,
                       "partial": not gpu_known,
                       "wall_source": wall_source, "meter_w": meter_w,
                       "modelled_w": modelled_w, "residual_w": residual_w,
                       "gpu": gpu, "mem": mem, "net": self.net.sample(now),
                       "load": loadavg(), "cpu_freq": self.cpu.freq_mhz(),
                       "disk": self._disk(), "cpu_power_source": self.cpu.source})
        self.latest = detail
        if gpu_known:
            # No fabricated point: the sparkline shows a shorter series rather
            # than a dip that never happened.
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
        self.gpu.close()


# --------------------------------------------------------------------------- totals


def _day_start(dt: datetime) -> float:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _month_start(dt: datetime) -> float:
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()


# Periods are half-open [start, end). Computed by normalising to local midnight
# rather than adding 86400, so a 23- or 25-hour daylight-saving day still ends
# where the calendar says it does.
def _next_day_start(dt: datetime) -> float:
    return _day_start(dt + timedelta(days=1))


def _next_month_start(dt: datetime) -> float:
    return _month_start(dt.replace(day=1) + timedelta(days=32))


def totals(store: Store, cfg: dict, now: datetime | None = None) -> dict:
    """Period totals.

    Standing charges are read back from the `days` table rather than
    recomputed from the current configuration, so editing the tariff cannot
    change what a closed day cost. Every period applies the same rule -- the
    charge for each day actually recorded -- so "all time" now means all
    electricity, which it previously did not.
    """
    now = now or datetime.now()
    out = {}
    windows = {"today": (_day_start(now), _next_day_start(now)),
               "month": (_month_start(now), _next_month_start(now)),
               "all": (0.0, None)}
    for label, (start, end) in windows.items():
        # Half-open, so "today" cannot pick up tomorrow if the clock moves.
        bound = "" if end is None else " AND hour < :end"
        args = {"start": int(start), "end": int(end or 0)}
        row = store.query(
            "SELECT COALESCE(SUM(wh),0) wh, COALESCE(SUM(wh_busy),0) busy,"
            " COALESCE(SUM(wh_idle),0) idle, COALESCE(SUM(cost),0) cost,"
            " COALESCE(MAX(max_w),0) peak, COALESCE(SUM(secs),0) secs,"
            " COALESCE(SUM(secs_missing),0) missing"
            " FROM hourly WHERE hour >= :start" + bound, args)[0]
        charges = store.query(
            "SELECT COALESCE(SUM(standing),0) standing, COUNT(*) days"
            " FROM days WHERE day >= :start" + bound.replace("hour", "day"), args)[0]
        energy_cost = row["cost"]
        standing_cost = charges["standing"]
        elapsed = row["secs"] + row["missing"]
        out[label] = {"kwh": row["wh"] / 1000.0, "busy_kwh": row["busy"] / 1000.0,
                      "idle_kwh": row["idle"] / 1000.0,
                      "cost": energy_cost + standing_cost,
                      # Split out, because they answer different questions:
                      # one scales with use, the other with calendar days.
                      "energy_cost": energy_cost, "standing_cost": standing_cost,
                      "days": charges["days"],
                      "peak_w": row["peak"], "hours": row["secs"] / 3600.0,
                      # What fraction of recorded time actually had every meter.
                      # Below 1.0 the figures above are an undercount, not a fall.
                      "hours_missing": row["missing"] / 3600.0,
                      "coverage": (row["secs"] / elapsed) if elapsed > 0 else 1.0}
    # Projection: the average draw over the time actually recorded, extended to a
    # 30-day month. Deliberately not scaled by elapsed calendar days -- in a month
    # where recording started on the 28th that would divide by ~28 and read near zero.
    recorded_h = out["month"]["hours"]
    rate = float(cfg["tariff"]["rate"]) if cfg["tariff"]["mode"] != "none" else 0.0
    # The projection looks forward, so it uses the tariff in effect now rather
    # than the recorded history.
    standing_now = float(cfg["tariff"].get("standing_charge_per_day", 0.0) or 0.0)
    if recorded_h > 0.01:
        avg_kw = out["month"]["kwh"] / recorded_h
        projected_kwh = avg_kw * 24.0 * 30.0
        out["month"]["projected_30d_kwh"] = projected_kwh
        out["month"]["projected_30d_cost"] = projected_kwh * rate + standing_now * 30.0
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


def health(monitor: Monitor, period_totals: dict, cfg: dict,
           now: float | None = None) -> dict:
    """Is the number on screen trustworthy right now?

    Three distinct questions, deliberately not merged into one boolean: is the
    sampler running, is every meter readable, and how much of the recorded
    history was complete. A client should be able to say which one is wrong,
    because the fixes differ.

    Levels: "error" means the reading is wrong or absent, "warn" means it is
    incomplete, "info" is a standing caveat rather than a fault.
    """
    now = now or time.time()
    interval = float(cfg["sampling"]["interval"])
    age = now - (monitor.latest.get("ts") or 0)
    # Matches /healthz: a few missed ticks is a hiccup, 30 s is a wedged sampler.
    sampling_ok = age < max(30.0, interval * 5)

    issues = []
    if not sampling_ok:
        issues.append({"level": "error", "code": "sampler_stalled",
                       "message": f"No sample for {age:.0f} s: the sampler is not running."})
    if monitor.latest.get("gpu", {}).get("error"):
        issues.append({"level": "error", "code": "gpu_unreadable",
                       "message": "GPU sensor unreadable: its power is missing from the total."})
    today = period_totals.get("today", {})
    coverage = today.get("coverage", 1.0)
    if coverage < 0.999:
        issues.append({"level": "warn", "code": "partial_coverage",
                       "message": f"{100 * (1 - coverage):.1f}% of today was not measured; "
                                  "energy and cost below are an undercount."})
    wall_source = monitor.latest.get("wall_source", "model")
    if monitor.meter.meter is not None and wall_source != "meter":
        issues.append({"level": "warn", "code": "meter_unreadable",
                       "message": "The wall meter is configured but not responding; "
                                  "power is modelled instead."})
    if monitor.cpu.source != "rapl":
        issues.append({"level": "info", "code": "cpu_estimated",
                       "message": "CPU power is modelled from utilisation, not measured "
                                  "(±20 W). See the README to enable RAPL."})
    return {
        "ok": sampling_ok and not any(i["level"] == "error" for i in issues),
        "last_sample_age_s": age,
        "coverage_today": coverage,
        "cpu_source": monitor.cpu.source,
        "gpu_source": monitor.gpu.source if monitor.gpu.enabled else None,
        "wall_source": wall_source,
        "residual_w": monitor.latest.get("residual_w"),
        "cpu_packages": monitor.cpu.packages,
        "gpu_count": monitor.latest.get("gpu", {}).get("count", 0),
        "issues": issues,
    }


def now_payload(monitor: Monitor, store: Store, cfg: dict) -> dict:
    latest = dict(monitor.latest)
    if not latest:
        latest = monitor.sample_once()
    gpu = latest.get("gpu", {})
    rate = monitor.rate
    tariff = cfg["tariff"]
    period_totals = totals(store, cfg)
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
                  "source": latest.get("cpu_power_source", "estimated"),
                  # True when a meter failed this sample: the total is missing
                  # that component and no energy was recorded for the interval.
                  "partial": bool(latest.get("partial", False)),
                  # "meter" = total_w is measured at the wall; "model" = it is
                  # derived from components plus configured constants.
                  "wall_source": latest.get("wall_source", "model"),
                  "meter_w": latest.get("meter_w"),
                  "modelled_w": latest.get("modelled_w"),
                  # meter minus model: what the configured baseline and PSU
                  # efficiency are getting wrong, in watts.
                  "residual_w": latest.get("residual_w")},
        "cpu": {"pct": latest["cpu_pct"], "temp": latest["cpu_temp"],
                "freq_mhz": latest.get("cpu_freq"), "load": latest.get("load"),
                "cores": monitor.cpu.ncpu, "max_w": float(cfg["cpu"]["max_w"])},
        "gpu": {"present": gpu.get("present", False), "name": gpu.get("name"),
                "util": gpu.get("util"), "temp": gpu.get("temp"), "fan": gpu.get("fan"),
                "clock_mhz": gpu.get("clock_mhz"), "mem_used": gpu.get("mem_used"),
                "mem_total": gpu.get("mem_total"), "limit_w": gpu.get("limit_w"),
                "procs": gpu.get("procs", []), "error": gpu.get("error", False),
                # Host aggregate above, per-device below. The aggregate fields
                # keep their shape so existing clients are unaffected.
                "count": gpu.get("count", 0), "devices": gpu.get("devices", [])},
        "mem": latest.get("mem", {}), "disk": latest.get("disk", {}),
        "net": latest.get("net", {}), "busy": bool(latest["busy"]),
        "totals": period_totals,
        "health": health(monitor, period_totals, cfg),
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
    trusted_proxies: list = []
    keys: list = []
    throttle: "Throttle" = None
    tls: bool = False

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
    # The policy lives in authorize() and effective_client() above, where it can
    # be tested without a socket. This end only gathers the inputs.
    def _client(self) -> str:
        return effective_client(self.client_address[0],
                                self.headers.get("X-Forwarded-For"),
                                self.trusted_proxies)

    def _check_token(self, route) -> tuple[bool, bool]:
        """Returns (authorised, token_came_from_query)."""
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

        if from_query:
            # Deprecated: a URL token reaches browser history, proxy logs and
            # Referer headers. Kept working, but say so.
            print("powermon: ?token= is deprecated and will be removed; use the "
                  "X-Powermon-Token header, or open the page once to set the cookie.",
                  file=sys.stderr, flush=True)

        client = self._client()
        allowed, key = authorize(
            client=client,
            token_supplied=supplied,
            keys=self.keys,
            require_token_always=bool(self.cfg["server"].get("require_token_always")),
        )
        if self.throttle:
            if allowed:
                self.throttle.record_success(client)
            else:
                # Only a failure waits. Delaying before the check would punish a
                # correct key for someone else's guessing on the same address --
                # which, behind an untrusted proxy, is every other client.
                delay = self.throttle.delay_for(client)
                self.throttle.record_failure(client)
                if delay:
                    time.sleep(delay)
        if not allowed:
            # The effective address, not just the peer: without it a proxy
            # misconfiguration is invisible.
            print(f"powermon: denied {self.command} {self.path} from {client}"
                  f" (peer {self.client_address[0]})", file=sys.stderr, flush=True)
        elif key is not None:
            self.matched_key = key
        return allowed, from_query

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
                    # Echo back what the client actually presented, which may be
                    # a named key rather than server.token.
                    supplied = (parse_qs(route.query).get("token") or [""])[0]
                    secure = "; Secure" if self.tls else ""
                    cookie = [("Set-Cookie",
                               f"powermon_token={supplied}; Max-Age=31536000; "
                               f"Path=/; SameSite=Lax; HttpOnly{secure}")]
                self._send(200, page.read_bytes(), "text/html; charset=utf-8", extra=cookie)
            elif path == "/api/now":
                self._json(now_payload(self.monitor, self.store, self.cfg))
            elif path == "/api/history":
                span = (parse_qs(route.query).get("range") or ["24h"])[0]
                if span not in ("24h", "7d", "30d", "90d"):
                    span = "24h"
                self._json({"range": span, "series": history(self.store, self.monitor, span)})
            elif path == "/healthz":
                # Same verdict as /api/now's health block, so a probe and the
                # dashboard can never disagree about whether this host is well.
                self._json(health(self.monitor, totals(self.store, self.cfg), self.cfg))
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


def add_key(cfg: dict, cfg_path: Path, name: str, scope: str = "read") -> int:
    """Mint a key, store only its hash, and print the token once."""
    if scope not in SCOPES:
        print(f"powermon: scope must be one of {', '.join(SCOPES)}", file=sys.stderr)
        return 2
    path = str(cfg["server"].get("keys_file") or "")
    if not path:
        path = "powermon-keys.json"
        print(f"powermon: server.keys_file is not set; using {path}.\n"
              f"          Add  keys_file = \"{path}\"  under [server] in {cfg_path}.",
              file=sys.stderr)
    full = Path(os.path.expanduser(path))
    if not full.is_absolute():
        full = cfg_path.parent / full
    try:
        entries = json.loads(full.read_text(encoding="utf-8"))
        entries = entries if isinstance(entries, list) else []
    except (OSError, ValueError):
        entries = []
    if any(e.get("name") == name for e in entries):
        print(f"powermon: a key named {name!r} already exists; "
              "revoke it first with --revoke-key.", file=sys.stderr)
        return 2

    token = secrets.token_urlsafe(24)
    entries.append({"name": name, "scope": scope, "hash": hash_token(token),
                    "created": datetime.now().strftime("%Y-%m-%d")})
    full.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    try:
        full.chmod(0o600)
    except OSError:
        pass
    # Only chance to see it: the file holds a hash, by design.
    print(f"key {name!r} ({scope}) added to {full}\n\n  {token}\n\n"
          "That token is not stored and cannot be shown again. "
          "Restart powermon to load it.")
    return 0


def revoke_key(cfg: dict, cfg_path: Path, name: str) -> int:
    path = str(cfg["server"].get("keys_file") or "powermon-keys.json")
    full = Path(os.path.expanduser(path))
    if not full.is_absolute():
        full = cfg_path.parent / full
    try:
        entries = json.loads(full.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(f"powermon: no key file at {full}", file=sys.stderr)
        return 2
    remaining = [e for e in entries if e.get("name") != name]
    if len(remaining) == len(entries):
        print(f"powermon: no key named {name!r} in {full}", file=sys.stderr)
        return 2
    full.write_text(json.dumps(remaining, indent=2) + "\n", encoding="utf-8")
    print(f"key {name!r} revoked. Restart powermon to apply.")
    return 0


def main() -> int:
    args = sys.argv[1:]
    # A config path may still be given positionally, as before.
    flags = {a for a in args if a.startswith("--")}
    positional = [a for a in args if not a.startswith("--")]

    def flag_value(flag: str) -> str | None:
        if flag in args:
            index = args.index(flag)
            return args[index + 1] if index + 1 < len(args) else None
        return None

    cfg_arg = next((p for p in positional if p.endswith(".toml")), None)
    cfg_path = Path(cfg_arg) if cfg_arg else HERE / "config.toml"
    cfg = load_config(cfg_path)

    if "--add-key" in flags:
        name = flag_value("--add-key")
        if not name:
            print("powermon: --add-key needs a name", file=sys.stderr)
            return 2
        return add_key(cfg, cfg_path, name, flag_value("--scope") or "read")
    if "--revoke-key" in flags:
        name = flag_value("--revoke-key")
        if not name:
            print("powermon: --revoke-key needs a name", file=sys.stderr)
            return 2
        return revoke_key(cfg, cfg_path, name)

    # Before anything is opened, sampled or bound.
    fatal = validate_server_config(cfg, cfg_path)
    if fatal:
        print(f"powermon: {fatal}", file=sys.stderr, flush=True)
        return 2
    Handler.trusted_proxies = parse_trusted_proxies(cfg["server"].get("trusted_proxies"))
    Handler.keys = load_keys(cfg, cfg_path.parent)
    Handler.throttle = Throttle()

    db_path = Path(os.path.expanduser(
        str(cfg["sampling"].get("db") or HERE / "powermon.db")))
    store = Store(db_path)
    monitor = Monitor(cfg, store)

    monitor.sample_once()  # prime deltas so the first HTTP hit has real data
    threading.Thread(target=monitor.run, name="sampler", daemon=True).start()

    Handler.monitor, Handler.store, Handler.cfg = monitor, store, cfg
    host, port = cfg["server"]["host"], int(cfg["server"]["port"])
    httpd = ThreadingHTTPServer((host, port), Handler)

    cert = str(cfg["server"].get("tls_cert") or "")
    tls_key = str(cfg["server"].get("tls_key") or "")
    scheme = "http"
    if cert and tls_key:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        try:
            context.load_cert_chain(os.path.expanduser(cert), os.path.expanduser(tls_key))
        except (OSError, ssl.SSLError) as exc:
            print(f"powermon: refusing to start: TLS certificate unusable: {exc}",
                  file=sys.stderr, flush=True)
            return 2
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
        Handler.tls = True
        scheme = "https"

    src = "RAPL (measured)" if monitor.cpu.source == "rapl" else "utilisation model (estimated)"
    named = [k["name"] for k in Handler.keys]
    print(f"powermon: {scheme}://{host}:{port}  db={db_path}"
          + (f"  keys: {', '.join(named)}" if named else "  (loopback only)"), flush=True)
    gpu_src = f" via {monitor.gpu.source}" if monitor.gpu.enabled else ""
    print(f"powermon: CPU power from {src}; "
          f"GPU: {monitor.gpu.name or 'none detected'}{gpu_src}", flush=True)
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
