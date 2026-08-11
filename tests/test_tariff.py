"""Billing semantics regression tests.

Run from the repository root:  python3 -m unittest discover -s tests

The product audit's P1: standing charge was recomputed from the current
configuration on every read, so editing the tariff silently rewrote what past
days had cost, and it was applied to today and month but not to all time, so
"all time" did not mean all electricity.
"""
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powermon import Store, _day_start, totals  # noqa: E402

WATTS = {"total": 1000.0, "cpu": 400.0, "gpu": 500.0, "other": 100.0}
RATE = 0.15


def cfg(rate=RATE, standing=0.0, mode="flat"):
    return {"tariff": {"rate": rate, "mode": mode, "standing_charge_per_day": standing}}


class BillingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "t.db")
        # Two days in the same month, and "now" on the second of them.
        self.day1 = datetime(2026, 8, 10, 12, 0, 0)
        self.day2 = datetime(2026, 8, 11, 12, 0, 0)

    def record_day(self, when: datetime, standing: float, hours: float = 1.0):
        """A day with one hour of 1 kW recorded, at the given standing charge."""
        start = _day_start(when)
        self.store.note_day(start, standing)
        self.store.add_energy(start, start + hours * 3600, WATTS, busy=False, rate=RATE)

    def test_editing_the_tariff_does_not_rewrite_a_closed_day(self):
        """The audit's acceptance criterion, stated literally."""
        self.record_day(self.day1, standing=0.30)
        before = totals(self.store, cfg(standing=0.30), now=self.day1)["today"]["cost"]

        # The next day the user changes their standing charge.
        self.record_day(self.day2, standing=0.90)
        after = totals(self.store, cfg(standing=0.90), now=self.day1)["today"]["cost"]

        self.assertAlmostEqual(after, before,
                               msg="yesterday's cost changed when today's tariff did")

    def test_all_time_includes_standing_charges(self):
        """Previously all-time silently excluded them, so the periods disagreed."""
        self.record_day(self.day1, standing=0.50)
        self.record_day(self.day2, standing=0.50)
        result = totals(self.store, cfg(standing=0.50), now=self.day2)

        self.assertAlmostEqual(result["all"]["standing_cost"], 1.00)
        self.assertAlmostEqual(result["today"]["standing_cost"], 0.50)
        self.assertAlmostEqual(result["month"]["standing_cost"], 1.00)

    def test_every_period_uses_the_same_inclusion_rule(self):
        self.record_day(self.day1, standing=0.40)
        self.record_day(self.day2, standing=0.40)
        result = totals(self.store, cfg(standing=0.40), now=self.day2)

        for label in ("today", "month", "all"):
            period = result[label]
            self.assertAlmostEqual(
                period["cost"], period["energy_cost"] + period["standing_cost"],
                msg=f"{label} does not split into energy plus standing")

    def test_energy_cost_is_separated_from_the_standing_charge(self):
        self.record_day(self.day1, standing=0.25)
        today = totals(self.store, cfg(standing=0.25), now=self.day1)["today"]

        # 1 kW for one hour = 1 kWh at 0.15 = 0.15, plus one day at 0.25.
        self.assertAlmostEqual(today["energy_cost"], 0.15, places=4)
        self.assertAlmostEqual(today["standing_cost"], 0.25, places=4)
        self.assertAlmostEqual(today["cost"], 0.40, places=4)

    def test_a_partly_monitored_day_is_charged_once_not_pro_rata(self):
        """The standing charge is a calendar fact: you pay it for the day."""
        self.record_day(self.day1, standing=0.60, hours=0.25)
        today = totals(self.store, cfg(standing=0.60), now=self.day1)["today"]

        self.assertAlmostEqual(today["standing_cost"], 0.60)
        self.assertEqual(today["days"], 1)

    def test_days_are_only_counted_when_something_was_recorded(self):
        """Unmonitored days are not billed: `hours` is recorded time, and the
        day count now follows the same rule."""
        self.record_day(self.day1, standing=0.60)
        month = totals(self.store, cfg(standing=0.60), now=self.day2)["month"]

        self.assertEqual(month["days"], 1, "a day with no samples must not be charged")
        self.assertAlmostEqual(month["standing_cost"], 0.60)

    def test_a_previous_month_is_excluded_from_this_month(self):
        july = datetime(2026, 7, 31, 12, 0, 0)
        self.record_day(july, standing=0.50)
        self.record_day(self.day1, standing=0.50)
        result = totals(self.store, cfg(standing=0.50), now=self.day1)

        self.assertAlmostEqual(result["month"]["standing_cost"], 0.50, msg="August only")
        self.assertAlmostEqual(result["all"]["standing_cost"], 1.00, msg="both months")

    def test_no_standing_charge_configured_is_the_common_case(self):
        self.record_day(self.day1, standing=0.0)
        today = totals(self.store, cfg(standing=0.0), now=self.day1)["today"]

        self.assertAlmostEqual(today["standing_cost"], 0.0)
        self.assertAlmostEqual(today["cost"], today["energy_cost"])

    def test_reconfiguring_within_an_open_day_updates_that_day(self):
        """Today is not closed yet, so correcting a wrong figure should apply."""
        start = _day_start(self.day1)
        self.store.note_day(start, 0.20)
        self.store.note_day(start, 0.35)   # user fixes the value the same day
        today = totals(self.store, cfg(standing=0.35), now=self.day1)["today"]

        self.assertAlmostEqual(today["standing_cost"], 0.35)
        self.assertEqual(today["days"], 1, "correcting a value must not add a day")

    def test_empty_database(self):
        result = totals(self.store, cfg(standing=0.50), now=self.day1)
        for label in ("today", "month", "all"):
            self.assertAlmostEqual(result[label]["cost"], 0.0)
            self.assertEqual(result[label]["days"], 0)


if __name__ == "__main__":
    unittest.main()
