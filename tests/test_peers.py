"""Several machines in one view: peer parsing, polling and the /api/peers shape.

Run from the repository root:  python3 -m unittest discover -s tests
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powermon import (EDITABLE, PeerPoller, parse_peers, peer_summary, peers_config,  # noqa: E402
                      peers_payload, peers_total)

SECRET = "s3cr3t-token"


def payload(w=300.0, cost_h=0.09, busy=True, today=1.5, month=20.0, kwh=80.0, symbol="EUR",
            host="box"):
    """The keys now_payload really emits, trimmed to the ones peers read."""
    return {"ts": 1000.0, "busy": busy,
            "power": {"total_w": w, "cost_per_h": cost_h},
            "totals": {"today": {"cost": today}, "month": {"cost": month, "kwh": kwh}},
            "meta": {"symbol": symbol, "host": host}}


def poller(fetch, names=("nas", "lab"), poll_s=15.0, clock=lambda: 1000.0):
    peers = [{"name": n, "url": f"http://{n}.lan:8787", "token": SECRET} for n in names]
    return PeerPoller(peers, poll_s, 1.0, fetch=fetch, clock=clock)


class ParseTests(unittest.TestCase):
    def test_good(self):
        got = parse_peers("nas=http://nas.lan:8787|abc123, lab=https://lab:8787/")
        self.assertEqual(got, [{"name": "nas", "url": "http://nas.lan:8787", "token": "abc123"},
                               {"name": "lab", "url": "https://lab:8787", "token": ""}])
        self.assertEqual(parse_peers(""), [])

    def test_bad(self):
        for text in ["nas", "=http://x:1", "a=ftp://x", "a=http://", "a=x:8787",
                     "a=http://x, a=http://y"]:
            with self.assertRaises(ValueError, msg=text):
                parse_peers(text)

    def test_error_names_entry_not_token(self):
        with self.assertRaises(ValueError) as cm:
            parse_peers(f"nas=ftp://x|{SECRET}")
        self.assertIn("nas", str(cm.exception))
        self.assertNotIn(SECRET, str(cm.exception))

    def test_config_and_not_editable(self):
        with self.assertRaises(ValueError):
            peers_config({"peers": {"hosts": "", "poll_s": 0, "timeout": 4}})
        self.assertNotIn("peers", EDITABLE)


class ShapeTests(unittest.TestCase):
    def machines(self, fetch, now=1000.0):
        p = poller(fetch)
        p.poll_once()
        return p.machines(now)

    def test_healthy(self):
        m = self.machines(lambda *a: payload())[0]
        self.assertTrue(m["ok"])
        self.assertEqual((m["total_w"], m["cost_per_h"], m["busy"], m["today_cost"],
                          m["month_cost"], m["month_kwh"], m["symbol"], m["age_s"]),
                         (300.0, 0.09, True, 1.5, 20.0, 80.0, "EUR", 0.0))
        self.assertEqual(m["url"], "http://nas.lan:8787")
        self.assertFalse(m["self"])

    def test_never_answered_is_null_not_zero(self):
        def boom(*a):
            raise OSError("down")
        m = self.machines(boom)[0]
        self.assertFalse(m["ok"])
        self.assertIsNone(m["age_s"])
        for key in ("total_w", "cost_per_h", "busy", "today_cost", "month_cost", "month_kwh"):
            self.assertIsNone(m[key], key)
        self.assertTrue(m["error"])

    def test_stale_keeps_figures(self):
        p = poller(lambda *a: payload())
        p.poll_once()
        m = p.machines(1000.0 + 3 * 15.0 + 1)[0]
        self.assertFalse(m["ok"])
        self.assertEqual(m["total_w"], 300.0)
        self.assertAlmostEqual(m["age_s"], 46.0)
        self.assertEqual(p.machines(1000.0 + 45.0)[0]["ok"], True)

    def test_missing_figure_stays_none(self):
        data = payload()
        data["totals"]["month"].pop("kwh")
        self.assertIsNone(peer_summary(data)["month_kwh"])
        with self.assertRaises(ValueError):
            peer_summary({"nope": 1})

    def test_totals_skip_not_ok(self):
        def row(ok, w, symbol="EUR"):
            return {"ok": ok, "total_w": w, "cost_per_h": w / 1000, "today_cost": 1.0,
                    "month_cost": 2.0, "month_kwh": 3.0, "symbol": symbol}
        t = peers_total([row(True, 100.0), row(False, 999.0), row(True, 200.0)])
        self.assertEqual((t["total_w"], t["today_cost"], t["month_kwh"]), (300.0, 2.0, 6.0))
        self.assertEqual((t["machines_ok"], t["machines"], t["mixed_currency"]), (2, 3, False))

    def test_mixed_currency_nulls_costs_only(self):
        t = peers_total([{"ok": True, "total_w": 1.0, "cost_per_h": 1.0, "today_cost": 1.0,
                          "month_cost": 1.0, "month_kwh": 1.0, "symbol": s} for s in ("EUR", "$")])
        self.assertTrue(t["mixed_currency"])
        self.assertIsNone(t["cost_per_h"])
        self.assertIsNone(t["month_cost"])
        self.assertEqual(t["total_w"], 2.0)
        self.assertEqual(t["month_kwh"], 2.0)

    def test_unknown_figure_makes_sum_unknown(self):
        good = {"ok": True, "total_w": 1.0, "cost_per_h": 1.0, "today_cost": 1.0,
                "month_cost": 1.0, "month_kwh": 1.0, "symbol": "EUR"}
        t = peers_total([good, {**good, "today_cost": None}])
        self.assertIsNone(t["today_cost"])
        self.assertEqual(t["month_cost"], 2.0)

    def test_payload_self_first_no_token(self):
        p = poller(lambda *a: payload())
        p.poll_once()
        out = peers_payload(payload(host="here"), p, 1000.0)
        self.assertEqual([m["name"] for m in out["machines"]], ["here", "nas", "lab"])
        self.assertTrue(out["machines"][0]["self"])
        self.assertIsNone(out["machines"][0]["url"])
        self.assertNotIn(SECRET, json.dumps(out))

    def test_no_peers_returns_self(self):
        out = peers_payload(payload(), None, 1000.0)
        self.assertEqual(len(out["machines"]), 1)
        self.assertEqual(out["total"]["machines"], 1)

    def test_url_credentials_stripped(self):
        p = PeerPoller([{"name": "a", "url": "http://u:pw@h:1", "token": ""}], 15, 1,
                       fetch=lambda *a: payload(), clock=lambda: 1000.0)
        p.poll_once()
        self.assertEqual(p.machines(1000.0)[0]["url"], "http://h:1")


class PollerTests(unittest.TestCase):
    def test_failing_peer_does_not_hide_the_others(self):
        seen = []

        def fetch(url, token, timeout):
            seen.append((url, token, timeout))
            if "nas" in url:
                raise TimeoutError(f"slow {url} {token}")
            return payload(w=50.0)
        p = poller(fetch, names=("nas", "lab", "pi"))
        p.poll_once()
        nas, lab, pi = p.machines(1000.0)
        self.assertFalse(nas["ok"])
        self.assertEqual(nas["error"], "timeout")
        self.assertNotIn(SECRET, json.dumps(nas))
        self.assertTrue(lab["ok"] and pi["ok"])
        self.assertEqual(lab["total_w"], 50.0)
        self.assertEqual(len(seen), 3)

    def test_later_failure_keeps_last_good(self):
        calls = iter([payload(w=70.0), OSError("x")])

        def fetch(*a):
            nxt = next(calls)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt
        p = poller(fetch, names=("nas",))
        p.poll_once()
        p.poll_once()
        m = p.machines(1000.0)[0]
        self.assertEqual(m["total_w"], 70.0)
        self.assertTrue(m["ok"])


if __name__ == "__main__":
    unittest.main()
