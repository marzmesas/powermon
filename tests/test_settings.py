"""The settings API: whitelist, validation, storage, live apply and write guards.

Run from the repository root:  python3 -m unittest discover -s tests
Nothing here runs a suspend command: the suspend window is never reached.
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powermon import (  # noqa: E402
    Handler, Monitor, build_policies, change_settings, host_is_local, load_config,
    parse_settings_body, settings_path, settings_payload, write_refusal,
)

TOML = """# my comments must survive
[tariff]
rate = 0.2   # what the bill says
standing_charge_per_day = 0.5
"""


class Dir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.cfg_path = self.dir / "config.toml"
        self.cfg_path.write_text(TOML)
        self.cfg = load_config(self.cfg_path)
        self.file = settings_path(self.cfg, self.cfg_path)

    def tearDown(self):
        self.tmp.cleanup()

    def change(self, body=None, reset=None):
        changes = parse_settings_body(json.dumps(body).encode()) if body is not None else None
        return change_settings(self.cfg, self.cfg_path, None, changes, reset)


class WhitelistTests(Dir):
    def test_file_only_keys_are_refused_by_name_and_nothing_is_saved(self):
        for body in ({"suspend": {"command": "rm -rf /"}}, {"server": {"token": "x"}},
                     {"tariff": {"dynamic_url": "http://x"}}, {"meter": {"type": "http"}},
                     {"tariff": {"rate": 0.3, "dynamic_headers": "A: b"}}, {"nope": {"x": 1}}):
            with self.assertRaises(ValueError) as ctx:
                self.change(body)
            section, values = next(iter(body.items()))
            self.assertIn(f"{section}.{list(values)[-1]}", str(ctx.exception))
        self.assertFalse(self.file.exists())

    def test_coercion(self):
        out = parse_settings_body(b'{"tariff": {"rate": "0.31", "mode": " TOU "},'
                                  b' "suspend": {"skip_if_logged_in": false}}')
        self.assertEqual(out, {"tariff": {"rate": 0.31, "mode": "tou"},
                               "suspend": {"skip_if_logged_in": False}})
        self.assertEqual(parse_settings_body(b'{"tariff": {"dynamic_surcharge": -0.01}}'),
                         {"tariff": {"dynamic_surcharge": -0.01}})

    def test_bad_values_are_refused(self):
        for raw in (b'{"tariff": {"rate": -1}}', b'{"tariff": {"rate": true}}',
                    b'{"tariff": {"rate": NaN}}', b'{"tariff": {"rate": Infinity}}',
                    b'{"tariff": {"rate": "1e999"}}', b'{"tariff": {"mode": "free"}}',
                    b'{"alerts": {"ntfy_url": "file:///etc/passwd"}}',
                    b'{"alerts": {"webhook_url": "ftp://x"}}',
                    b'{"tariff": {"symbol": "a\\nb"}}', b'{"suspend": {"skip_if_logged_in": 1}}',
                    b'[1]', b'{}', b'not json', b'{"tariff": 3}'):
            with self.assertRaises(ValueError, msg=raw):
                parse_settings_body(raw)


class StorageTests(Dir):
    def test_invalid_whole_config_leaves_the_file_untouched(self):
        self.change({"tariff": {"rate": 0.3}})
        before = self.file.read_bytes()
        with self.assertRaises(ValueError):
            self.change({"tariff": {"mode": "tou"}})            # no tou_weekday
        with self.assertRaises(ValueError):
            self.change({"tariff": {"mode": "dynamic", "dynamic_source": "energy-charts"}})
        self.assertEqual(self.file.read_bytes(), before)

    def test_overrides_merge_over_toml_and_survive_a_reload(self):
        self.change({"tariff": {"rate": 0.31}, "alerts": {"idle_w": 150}})
        cfg = load_config(self.cfg_path)
        self.assertEqual(cfg["tariff"]["rate"], 0.31)
        self.assertEqual(cfg["tariff"]["standing_charge_per_day"], 0.5)   # TOML still applies
        self.assertEqual(cfg["alerts"]["idle_w"], 150.0)
        self.assertEqual(self.cfg_path.read_text(), TOML)                   # never rewritten

    def test_reset_restores_the_toml_value(self):
        new, _, _ = self.change({"tariff": {"rate": 0.31, "tax_pct": 19}})
        self.cfg = new
        new, _, touched = self.change(reset="tariff.rate")
        self.assertEqual(new["tariff"]["rate"], 0.2)
        self.assertEqual(touched, ["tariff.rate"])
        self.assertEqual(json.loads(self.file.read_text()), {"tariff": {"tax_pct": 19.0}})
        with self.assertRaises(ValueError):
            self.change(reset="suspend.command")

    def test_atomic_write_leaves_valid_private_json_and_no_temp_files(self):
        self.change({"tariff": {"rate": 0.31}})
        self.assertEqual(json.loads(self.file.read_text()), {"tariff": {"rate": 0.31}})
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["config.toml", "settings.json"])

    def test_hand_added_file_only_key_is_ignored_on_load(self):
        self.file.write_text('{"suspend": {"command": "evil"}, "tariff": {"rate": 0.4}}')
        cfg = load_config(self.cfg_path)
        self.assertEqual(cfg["suspend"]["command"], "systemctl suspend")
        self.assertEqual(cfg["tariff"]["rate"], 0.4)


class PayloadTests(Dir):
    def test_read_scope_gets_redacted_urls(self):
        self.cfg, _, _ = self.change({"alerts": {"ntfy_url": "https://ntfy.sh/secret-topic"}})
        admin = settings_payload(self.cfg, self.cfg_path, True)
        self.assertEqual(admin["values"]["alerts"]["ntfy_url"], "https://ntfy.sh/secret-topic")
        self.assertEqual(admin["overridden"], ["alerts.ntfy_url"])
        self.assertTrue(admin["editable"])
        read = settings_payload(self.cfg, self.cfg_path, False)
        self.assertEqual(read["values"]["alerts"]["ntfy_url"], "")
        self.assertIn("alerts.ntfy_url", read["redacted"])
        self.assertFalse(read["editable"])
        self.assertNotIn("command", read["values"]["suspend"])
        self.assertNotIn("server", read["values"])

    def test_writable_off_means_not_editable(self):
        self.cfg["server"]["settings_writable"] = False
        self.assertFalse(settings_payload(self.cfg, self.cfg_path, True)["editable"])


GOOD = {"Content-Type": "application/json", "X-Powermon-Write": "1",
        "Host": "127.0.0.1:8787", "Content-Length": "20"}


def refusal(keyless=True, method="POST", **changes):
    headers = {k: v for k, v in {**GOOD, **changes}.items() if v is not None}
    return write_refusal(method, headers, keyless, {"box"})


class WriteGuardTests(unittest.TestCase):
    def test_well_formed_request_passes(self):
        self.assertIsNone(refusal())
        self.assertIsNone(refusal(Origin="http://127.0.0.1:8787"))
        self.assertIsNone(refusal(method="DELETE", **{"Content-Length": None}))

    def test_missing_custom_header(self):
        self.assertEqual(refusal(**{"X-Powermon-Write": None})[0], 403)

    def test_wrong_content_type(self):
        for ctype in (None, "text/plain", "application/x-www-form-urlencoded",
                      "multipart/form-data", "application/json; charset=utf-8"):
            self.assertEqual(refusal(**{"Content-Type": ctype})[0], 403, ctype)

    def test_cross_origin(self):
        for origin in ("http://evil.example", "null", "http://127.0.0.1:9999"):
            self.assertEqual(refusal(Origin=origin)[0], 403, origin)

    def test_dns_rebinding_host_is_refused_without_a_key(self):
        # Under rebinding Origin and Host agree, so agreement proves nothing.
        r = refusal(Host="evil.example:8787", Origin="http://evil.example:8787")
        self.assertEqual(r[0], 403)
        self.assertIn("evil.example", r[1])
        # With a key, a proxy's own domain is fine.
        self.assertIsNone(refusal(keyless=False, Host="power.example.org"))

    def test_oversize_or_missing_body_length(self):
        self.assertEqual(refusal(**{"Content-Length": str(16 * 1024 + 1)})[0], 413)
        self.assertEqual(refusal(**{"Content-Length": "0"})[0], 413)
        self.assertEqual(refusal(**{"Content-Length": None})[0], 411)

    def test_host_is_local(self):
        for host in ("127.0.0.1:8787", "[::1]:8787", "localhost", "LOCALHOST:1",
                     "192.168.1.20:8787", "box", "box:8787", "a.localhost"):
            self.assertTrue(host_is_local(host, {"box"}), host)
        for host in ("", None, "evil.example", "box.evil.example", "localhost.evil.example"):
            self.assertFalse(host_is_local(host, {"box"}), host)


class FakeMonitor:
    apply_settings = Monitor.apply_settings

    def __init__(self, cfg):
        self.cfg = cfg
        self.apply_settings(cfg, build_policies(cfg, None))


class LiveApplyTests(Dir):
    def test_swap_keeps_alert_episode_and_restarts_suspend(self):
        m = FakeMonitor(self.cfg)
        self.cfg, policies, _ = self.change({"alerts": {"idle_w": 100, "after_min": 0},
                                             "suspend": {"after_idle_min": 30}})
        m.apply_settings(self.cfg, policies)
        m.alert._notifier.push = lambda *a: None
        m.alert.observe(0, 200, False)
        m.alert.observe(1, 200, False)
        self.assertTrue(m.alert.entry())
        m.suspend._since = 0.0
        self.cfg, policies, _ = self.change({"alerts": {"idle_w": 120}, "tariff": {"rate": 0.5}})
        m.apply_settings(self.cfg, policies)
        self.assertTrue(m.alert.entry())          # same episode, not a fresh one
        self.assertEqual(m.alert.idle_w, 120.0)
        self.assertIsNone(m.suspend._since)       # countdown restarted
        self.assertEqual(m.tariff.flat_rate, 0.5)
        self.assertEqual(m.cfg["tariff"]["rate"], 0.5)
        # Switched off: the episode is dropped rather than left active forever.
        self.cfg, policies, _ = self.change({"alerts": {"idle_w": 0}})
        m.apply_settings(self.cfg, policies)
        self.assertIsNone(m.alert.entry())


class SocketTests(Dir):
    def test_post_get_delete_round_trip(self):
        m = FakeMonitor(self.cfg)
        H = type("H", (Handler,), {"cfg": self.cfg, "cfg_path": self.cfg_path, "monitor": m,
                                   "store": None, "keys": [], "throttle": None,
                                   "trusted_proxies": []})
        server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}/api/settings"
        write = {"Content-Type": "application/json", "X-Powermon-Write": "1"}

        def call(method, url=base, body=None, headers=write):
            req = urllib.request.Request(url, method=method, headers=headers,
                                         data=json.dumps(body).encode() if body else None)
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    return r.status, json.load(r)
            except urllib.error.HTTPError as e:
                return e.code, json.load(e)

        status, out = call("POST", body={"tariff": {"rate": 0.31}})
        self.assertEqual(status, 200, out)
        self.assertEqual(out["values"]["tariff"]["rate"], 0.31)
        self.assertEqual(m.tariff.flat_rate, 0.31)
        self.assertEqual(call("GET", headers={})[1]["overridden"], ["tariff.rate"])

        self.assertEqual(call("POST", body={"tariff": {"rate": 1}}, headers={
            "Content-Type": "text/plain"})[0], 403)
        status, out = call("POST", body={"suspend": {"command": "x"}})
        self.assertEqual(status, 400)
        self.assertIn("suspend.command", out["error"])

        status, out = call("DELETE", url=base + "?key=tariff.rate")
        self.assertEqual(status, 200, out)
        self.assertEqual(out["values"]["tariff"]["rate"], 0.2)
        self.assertEqual(out["overridden"], [])
        self.assertEqual(m.tariff.flat_rate, 0.2)

        self.cfg["server"]["settings_writable"] = False
        self.assertEqual(call("POST", body={"tariff": {"rate": 0.4}})[0], 403)
        self.assertEqual(json.loads(settings_path(self.cfg, self.cfg_path).read_text()), {})


if __name__ == "__main__":
    unittest.main()
