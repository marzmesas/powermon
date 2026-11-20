"""Tariff modes: flat, time of day, dynamic, and taxes on top.

Run from the repository root:  python3 -m unittest discover -s tests
"""
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powermon import Tariff  # noqa: E402

DAY = "0-8=0.09, 8-10=0.14, 10-14=0.20, 14-18=0.14, 18-22=0.20, 22-24=0.14"


def tariff(fetch=None, **overrides):
    return Tariff({"tariff": {"mode": "flat", "rate": 0.15, **overrides}}, fetch=fetch)


def at(*when):
    return datetime(*when).timestamp()


class TariffTests(unittest.TestCase):
    def test_flat_and_none(self):
        self.assertEqual(tariff().rate_at(at(2026, 11, 19, 12)), 0.15)
        self.assertEqual(tariff(mode="none").rate_at(at(2026, 11, 19, 12)), 0.0)

    def test_tax_applies_to_rate_and_standing_charge(self):
        t = tariff(tax_pct=19, standing_charge_per_day=1.0)
        self.assertAlmostEqual(t.rate_at(0), 0.15 * 1.19)
        self.assertAlmostEqual(t.standing, 1.19)

    def test_time_of_use_weekday_weekend_and_holiday(self):
        t = tariff(mode="tou", tou_weekday=DAY, tou_weekend="0-24=0.09",
                   tou_holidays="2026-12-08")
        self.assertEqual(t.rate_at(at(2026, 11, 19, 3)), 0.09)        # Thursday, night
        self.assertEqual(t.rate_at(at(2026, 11, 19, 9, 59)), 0.14)    # shoulder
        self.assertEqual(t.rate_at(at(2026, 11, 19, 10)), 0.20)       # peak starts
        self.assertEqual(t.rate_at(at(2026, 11, 19, 23, 59)), 0.14)
        self.assertEqual(t.rate_at(at(2026, 11, 21, 12)), 0.09)       # Saturday
        self.assertEqual(t.rate_at(at(2026, 12, 8, 12)), 0.09)        # holiday on a Tuesday

    def test_time_of_use_gap_falls_back_to_rate_and_weekend_defaults_to_weekday(self):
        t = tariff(mode="tou", tou_weekday="8-20=0.20")
        self.assertEqual(t.rate_at(at(2026, 11, 19, 3)), 0.15)
        self.assertEqual(t.rate_at(at(2026, 11, 21, 12)), 0.20)

    def test_bad_config_is_refused(self):
        for bad in ({"mode": "tou"}, {"mode": "tou", "tou_weekday": "10-25=0.2"},
                    {"mode": "tou", "tou_weekday": "morning=cheap"}, {"mode": "hourly"}, {"mode": "pvpc"}):
            with self.assertRaises(ValueError, msg=bad):
                tariff(**bad)

    def test_dynamic_needs_a_usable_source(self):
        for bad in ({}, {"dynamic_source": "magic"}, {"dynamic_source": "energy-charts"},
                    {"dynamic_source": "energy-charts", "dynamic_zone": "DE LU&x=1"},
                    {"dynamic_source": "http", "dynamic_url": "file:///etc/passwd"}):
            with self.assertRaises(ValueError, msg=bad):
                tariff(mode="dynamic", **bad)

    def test_dynamic_price_gets_scale_surcharge_then_tax(self):
        noon = at(2026, 11, 19, 12)
        t = tariff(mode="dynamic", dynamic_source="energy-charts", dynamic_zone="DE-LU",
                   dynamic_surcharge=0.18, tax_pct=19,
                   fetch=lambda ts: {noon: 0.10, noon + 3600: 0.12})
        t._tried = noon                        # no background fetch from the test
        self.assertAlmostEqual(t.rate_at(noon + 60), 0.15 * 1.19)     # nothing fetched yet
        self.assertEqual(t.source, "fallback")
        t.load(noon)
        self.assertAlmostEqual(t.rate_at(noon + 60), (0.10 + 0.18) * 1.19)
        self.assertEqual(t.source, "energy-charts")
        self.assertAlmostEqual(t.rate_at(noon + 3660), (0.12 + 0.18) * 1.19)
        self.assertAlmostEqual(t.rate_at(noon + 7260), 0.15 * 1.19)   # past the published series
        self.assertAlmostEqual(t.rate_at(noon - 60), 0.15 * 1.19)     # before it

    def test_quarter_hour_series_does_not_stretch_its_last_slot(self):
        start = at(2026, 11, 19, 23)
        t = tariff(mode="dynamic", dynamic_source="pvpc",
                   fetch=lambda ts: {start + 900 * i: 0.10 + i / 100 for i in range(4)})
        t._tried = 1e12
        t.load(start)
        self.assertAlmostEqual(t.rate_at(start + 2700 + 899), 0.13)   # 23:45 slot
        self.assertEqual(t.rate_at(start + 3600), 0.15)               # midnight: not published

    def test_negative_spot_prices_pass_through(self):
        noon = at(2026, 11, 19, 12)
        t = tariff(mode="dynamic", dynamic_source="pvpc", dynamic_surcharge=0.02,
                   fetch=lambda ts: {noon: -0.05})
        t._tried = 1e12
        t.load(noon)
        self.assertAlmostEqual(t.rate_at(noon), -0.03)

    def test_fetch_failure_keeps_the_fallback(self):
        def broken(ts):
            raise OSError("offline")
        t = tariff(mode="dynamic", dynamic_source="pvpc", fetch=broken)
        t.load(0)
        t._tried = 1e12
        self.assertEqual(t.rate_at(at(2026, 11, 19, 12)), 0.15)
        self.assertEqual(t.source, "fallback")

    def test_http_source_reads_the_current_price_and_polls(self):
        t = tariff(mode="dynamic", dynamic_source="http", dynamic_url="http://ha.local/price",
                   dynamic_scale=0.01, fetch=lambda ts: {ts: 31.5})       # source in cents
        self.assertEqual(t._poll, 300.0)
        t._tried = 1e12
        t.load(1000.0)
        self.assertAlmostEqual(t.rate_at(1200.0), 0.315)

if __name__ == "__main__":
    unittest.main()
