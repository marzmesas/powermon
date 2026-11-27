"""The terminal client: sentence wording, scale, bars, cost line, colour rules, width."""
import copy
import importlib.machinery
import importlib.util
import json
import re
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "macos" / "PowermonMenuBarTests" / "Fixtures"
_loader = importlib.machinery.SourceFileLoader("pwr", str(ROOT / "pwr"))
pwr = importlib.util.module_from_spec(importlib.util.spec_from_loader("pwr", _loader))
_loader.exec_module(pwr)  # the __main__ guard keeps this from running main()

ANSI = re.compile(r"\x1b\[[0-9;]*m")
NOW = 1_000_000.0


def fixture(name="sample-now", edit=None):
    d = json.loads((FIX / f"{name}.json").read_text())
    d["ts"] = NOW
    d["power"].update(total_w=475.0, cpu_w=74.0, gpu_w=319.0, other_w=82.0, cost_per_h=0.1425, source="rapl")
    if edit:
        edit(d)
    return d


def say(d, now=NOW):
    return pwr.plain(pwr.speak(d, now))


def no_tariff(d):
    d["meta"]["mode"] = "none"


def strip(text):
    return ANSI.sub("", text)


class Sentence(unittest.TestCase):
    def test_idle(self):
        self.assertEqual(say(fixture()), "Idle at 475 W. That is €3.42 a day for doing nothing.")

    def test_working_without_run(self):
        self.assertEqual(say(fixture(edit=lambda d: d.update(busy=True))), "Working: 475 W, which is €0.14 an hour.")

    def test_working_with_run(self):
        d = fixture(edit=lambda d: d.update(busy=True, run={"start": NOW - 600, "kwh": 0.1, "cost": 0.8}))
        self.assertTrue(say(d).startswith("Working: 475 W, which is €0.14 an hour. This run has cost €0.80 since "))
        roles = [(r, t) for r, t in pwr.speak(d, NOW) if r != "plain"]
        self.assertEqual(roles, [("watts", "475 W"), ("money", "€0.14 an hour"), ("money", "€0.80")])

    def test_idle_alert_names_minutes_only_when_known(self):
        d = fixture(edit=lambda d: d.update(alerts=[{"kind": "idle", "message": "m", "since": NOW - 720}]))
        self.assertEqual(say(d), "Idle, but drawing 475 W for 12 minutes, above the idle alert threshold. That is €3.42 a day.")
        d = fixture(edit=lambda d: d.update(alert={"active": True}))
        self.assertEqual(say(d), "Idle, but drawing 475 W, above the idle alert threshold. That is €3.42 a day.")

    def test_other_alerts_are_appended(self):
        d = fixture(edit=lambda d: d.update(alerts=[{"kind": "gpu_hot", "message": "GPU at 88 °C"}, {"kind": "disk", "message": "x"}]))
        self.assertEqual(say(d), "Idle at 475 W. That is €3.42 a day for doing nothing. Alert: GPU at 88 °C, and 1 more.")

    def test_no_tariff_says_energy_not_money(self):
        d = fixture(edit=lambda d: (no_tariff(d), d.update(busy=True)))
        self.assertEqual(say(d), "Working: 475 W.")
        self.assertTrue(all(r != "money" for r, _ in pwr.speak(d, NOW)))
        self.assertEqual(say(fixture(edit=no_tariff)), "Idle at 475 W. That is 11.40 kWh a day for doing nothing.")

    def test_stale(self):
        self.assertEqual(say(fixture(), NOW + 30 + 10), "No reading for 40 seconds. The figures are the last ones received.")
        self.assertEqual(say(fixture(), NOW + 300), "No reading for 5 minutes. The figures are the last ones received.")


class Scale(unittest.TestCase):
    def test_nice_max_is_strictly_above(self):
        # steps of 50 below 200, 100 below 600, 200 beyond; min 100; exactly on a step goes up
        for watts, want in ((0, 100), (104, 150), (150, 200), (458.7, 500), (500, 600), (600, 800), (1234, 1400)):
            self.assertEqual(pwr.nice_max(watts), want, watts)

    def test_scale_uses_the_largest_recorded_peak(self):
        d = fixture(edit=lambda d: d["totals"]["month"].update(peak_w=458.7))
        self.assertIn("500 W", strip(pwr.render(d, None, 64, NOW)))

    def test_bar_cells(self):
        self.assertEqual(pwr.bar_cells(250, 500, 40), 20)
        self.assertEqual(pwr.bar_cells(0, 500, 40), 0)
        self.assertEqual(pwr.bar_cells(None, 500, 40), 0)
        self.assertEqual(pwr.bar_cells(0.5, 500, 40), 1)  # real draw is never invisible
        self.assertEqual(pwr.bar_cells(900, 500, 40), 40)

    def test_rows_and_no_gpu(self):
        text = strip(pwr.render(fixture(), None, 64, NOW))
        self.assertTrue(all(re.search(rf"^ {k}\s", text, re.M) for k in ("GPU", "CPU", "REST")))
        text = strip(pwr.render(fixture("gpu-absent"), None, 64, NOW))
        self.assertNotRegex(text, r"(?m)^ GPU\s")
        self.assertNotIn("VRAM", text)
        self.assertRegex(text, r"REST .* 401 W")  # total minus CPU


class CostLine(unittest.TestCase):
    def line(self, width, edit=None):
        d = fixture(edit=lambda d: (d["totals"]["month"].update(idle_cost=10.41), edit and edit(d)))
        return plain_line(pwr.costline(d, width))

    def test_drops_projected_then_idle(self):
        self.assertIn("IDLE", self.line(64))
        self.assertIn("PROJECTED", self.line(64))
        narrow = self.line(48)
        self.assertNotIn("PROJECTED", narrow)
        self.assertIn("IDLE", narrow)
        narrower = self.line(30)
        self.assertNotIn("IDLE", narrower)
        self.assertIn("MONTH", narrower)

    def test_idle_only_when_known(self):
        self.assertNotIn("IDLE", plain_line(pwr.costline(fixture(), 64)))

    def test_no_tariff_has_no_currency(self):
        text = strip(pwr.render(fixture(edit=no_tariff), "256", 64, NOW))
        self.assertNotIn("€", text)
        self.assertIn("kWh", text)


def plain_line(segs):
    return pwr.plain(segs)


class Robustness(unittest.TestCase):
    BARE = {"ts": NOW, "power": {"total_w": 104.0}, "meta": {"mode": "none"}}

    def test_empty_payload_does_not_crash(self):
        for d in ({}, {"ts": NOW}, self.BARE, {"power": None, "totals": None, "gpu": None}):
            pwr.render(d, "256", 64, NOW)

    def test_old_server_prints_no_invented_zero(self):
        def old(d):
            for k in ("run", "alerts", "suspend", "alert", "health"):
                d.pop(k, None)
            for t in d["totals"].values():
                for k in ("peak_w", "idle_cost", "idle_kwh", "co2_kg", "co2_g"):
                    t.pop(k, None)
        text = strip(pwr.render(fixture(edit=old), None, 64, NOW))
        body = [l for l in text.splitlines() if not l.lstrip().startswith("0 ") and "updated" not in l]
        self.assertNotRegex("\n".join(body), r"(?<![\d.:])0(\.0+)? ?(W|kWh|°C|%)|€0(\.0+)?(?![\d.])")
        self.assertNotRegex(text, r"IDLE [€\d]")

    def test_missing_values_show_a_dash(self):
        text = strip(pwr.render({"ts": NOW, "power": {}}, None, 64, NOW))
        self.assertIn("- W", text)


class Colour(unittest.TestCase):
    def test_mode_rules(self):
        cm = pwr.color_mode
        self.assertIsNone(cm(False, {}))
        self.assertIsNone(cm(True, {"NO_COLOR": "1"}))
        self.assertIsNone(cm(True, {}, no_color=True))
        self.assertEqual(cm(True, {"COLORTERM": "truecolor"}), "true")
        self.assertEqual(cm(True, {"COLORTERM": "24bit"}), "true")
        self.assertEqual(cm(True, {}), "256")
        self.assertEqual(cm(False, {}, force=True), "256")
        self.assertIsNone(cm(False, {}, no_color=True, force=True))

    def test_plain_output_has_no_escapes_and_stays_legible(self):
        text = pwr.render(fixture(), None, 64, NOW)
        self.assertNotIn("\x1b", text)
        self.assertIn("[IDLE]", text)
        self.assertIn("█", text)
        self.assertIn("░", text)

    def test_colour_variants_and_reset(self):
        for mode, snippet in (("true", "38;2;78;234;172"), ("256", "38;5;")):
            for edit in (None, lambda d: d.update(busy=True)):
                text = pwr.render(fixture(edit=edit), mode, 64, NOW)
                self.assertIn(snippet, text)
                for line in text.splitlines():
                    codes = ANSI.findall(line)
                    if not codes:
                        continue
                    self.assertEqual(codes[-1], "\x1b[0m", repr(line))  # never leaves a style open
                    self.assertEqual(codes.count("\x1b[0m") * 2, len(codes), repr(line))  # every open is closed
        self.assertNotIn("38;2", pwr.render(fixture(), "256", 64, NOW))

    def test_chip_states(self):
        cases = {"working": lambda d: d.update(busy=True), "idle": None,
                 "alert": lambda d: d.update(alert={"active": True}),
                 "nodata": lambda d: d.update(ts=NOW - 600)}
        for word, edit in (("WORKING", cases["working"]), ("IDLE", None), ("ALERT", cases["alert"]), ("NO DATA", cases["nodata"])):
            self.assertIn(f"[{word}]", pwr.render(fixture(edit=edit), None, 64, NOW))


class Width(unittest.TestCase):
    def test_every_line_fits(self):
        def hot(d):
            d.update(busy=True, run={"start": NOW - 90000, "cost": 123.4, "kwh": 5},
                     alerts=[{"kind": "idle", "since": NOW - 60, "message": "m"},
                             {"kind": "x", "message": "A long alert message " * 6}],
                     suspend={"enabled": True, "in_s": 120},
                     health={"issues": [{"level": "error", "message": "bad " * 40}]})
            d["totals"]["month"]["idle_cost"] = 1234.5
        for name in ("sample-now", "no-procs", "gpu-absent"):
            for edit in (None, hot):
                for cols in (30, 44, 64, 80, 200):
                    for mode in (None, "true"):
                        text = pwr.render(fixture(name, edit), mode, cols, NOW)
                        for line in strip(text).splitlines():
                            self.assertLessEqual(len(line), 80, line)
                            self.assertLessEqual(len(line), max(44, min(64, cols)), line)


if __name__ == "__main__":
    unittest.main()
