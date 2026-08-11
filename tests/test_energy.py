"""Energy ledger regression tests.

Run from the repository root:  python3 -m unittest discover -s tests

These cover the product audit's second P0: a failed GPU read was stored as
0 W and integrated as zero energy, so a driver hiccup silently lowered the
recorded consumption and cost. Missing data must reduce coverage, never
consumption.
"""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powermon import Store, totals  # noqa: E402

WATTS = {"total": 200.0, "cpu": 50.0, "gpu": 120.0, "other": 30.0}
HOUR = 1_786_200_000 // 3600 * 3600  # an exact hour boundary
RATE = 0.15


class EnergyLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "test.db")

    def hourly(self, hour=HOUR):
        rows = self.store.query("SELECT * FROM hourly WHERE hour = ?", (hour,))
        return dict(rows[0]) if rows else {}

    def test_complete_interval_integrates_normally(self):
        self.store.add_energy(HOUR, HOUR + 3600, WATTS, busy=False, rate=RATE)
        row = self.hourly()

        self.assertAlmostEqual(row["wh"], 200.0)        # 200 W for one hour
        self.assertAlmostEqual(row["gpu_wh"], 120.0)
        self.assertAlmostEqual(row["secs"], 3600.0)
        self.assertAlmostEqual(row["secs_missing"], 0.0)
        self.assertAlmostEqual(row["cost"], 0.2 * RATE)

    def test_incomplete_interval_adds_no_energy_cost_or_peak(self):
        self.store.add_energy(HOUR, HOUR + 3600, WATTS, busy=False, rate=RATE,
                              complete=False)
        row = self.hourly()

        for column in ("wh", "wh_busy", "wh_idle", "gpu_wh", "cpu_wh", "other_wh",
                       "cost", "secs", "max_w"):
            self.assertAlmostEqual(row[column], 0.0, msg=column)
        # The time is not lost, it is recorded as unmeasured.
        self.assertAlmostEqual(row["secs_missing"], 3600.0)

    def test_a_gpu_failure_cannot_reduce_accumulated_energy(self):
        """The audit's acceptance criterion, stated literally."""
        self.store.add_energy(HOUR, HOUR + 1800, WATTS, busy=True, rate=RATE)
        good = self.hourly()

        # The sensor drops out for the rest of the hour.
        self.store.add_energy(HOUR + 1800, HOUR + 3600, WATTS, busy=True, rate=RATE,
                              complete=False)
        after = self.hourly()

        self.assertAlmostEqual(after["wh"], good["wh"])
        self.assertAlmostEqual(after["gpu_wh"], good["gpu_wh"])
        self.assertAlmostEqual(after["cost"], good["cost"])
        self.assertGreater(after["wh"], 0.0)

    def test_missing_time_is_visible_as_coverage_not_as_a_dip(self):
        self.store.add_energy(HOUR, HOUR + 1800, WATTS, busy=False, rate=RATE)
        self.store.add_energy(HOUR + 1800, HOUR + 3600, WATTS, busy=False, rate=RATE,
                              complete=False)
        row = self.hourly()

        self.assertAlmostEqual(row["secs"], 1800.0)
        self.assertAlmostEqual(row["secs_missing"], 1800.0)
        self.assertAlmostEqual(row["secs"] / (row["secs"] + row["secs_missing"]), 0.5)

    def test_incomplete_intervals_still_split_across_hour_boundaries(self):
        self.store.add_energy(HOUR + 3000, HOUR + 4200, WATTS, busy=False, rate=RATE,
                              complete=False)

        self.assertAlmostEqual(self.hourly(HOUR)["secs_missing"], 600.0)
        self.assertAlmostEqual(self.hourly(HOUR + 3600)["secs_missing"], 600.0)

    def test_peak_watts_survives_a_later_incomplete_interval(self):
        self.store.add_energy(HOUR, HOUR + 60, WATTS, busy=True, rate=RATE)
        peak = self.hourly()["max_w"]
        self.store.add_energy(HOUR + 60, HOUR + 120, WATTS, busy=True, rate=RATE,
                              complete=False)

        self.assertAlmostEqual(self.hourly()["max_w"], peak)


class CoverageInTotalsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "test.db")
        self.cfg = {"tariff": {"rate": RATE, "mode": "flat", "standing_charge_per_day": 0.0}}

    def test_full_coverage_when_nothing_is_missing(self):
        self.store.add_energy(HOUR, HOUR + 3600, WATTS, busy=False, rate=RATE)
        self.assertAlmostEqual(totals(self.store, self.cfg)["all"]["coverage"], 1.0)

    def test_coverage_falls_when_a_meter_was_unavailable(self):
        self.store.add_energy(HOUR, HOUR + 2700, WATTS, busy=False, rate=RATE)
        self.store.add_energy(HOUR + 2700, HOUR + 3600, WATTS, busy=False, rate=RATE,
                              complete=False)
        all_time = totals(self.store, self.cfg)["all"]

        self.assertAlmostEqual(all_time["coverage"], 0.75)
        self.assertAlmostEqual(all_time["hours_missing"], 0.25)
        self.assertAlmostEqual(all_time["hours"], 0.75)

    def test_empty_database_reports_full_coverage_not_a_divide_by_zero(self):
        self.assertAlmostEqual(totals(self.store, self.cfg)["all"]["coverage"], 1.0)


class MigrationTests(unittest.TestCase):
    def test_an_existing_database_gains_the_column(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "old.db"

        # A database written by the previous version: no secs_missing column.
        old = sqlite3.connect(path)
        old.execute(
            "CREATE TABLE hourly (hour INTEGER PRIMARY KEY, wh REAL, wh_busy REAL,"
            " wh_idle REAL, gpu_wh REAL, cpu_wh REAL, other_wh REAL, cost REAL,"
            " rate REAL, secs REAL, max_w REAL)")
        old.execute("INSERT INTO hourly VALUES (?,100,0,100,60,30,10,0.015,0.15,3600,120)",
                    (HOUR,))
        old.commit()
        old.close()

        store = Store(path)  # must migrate rather than fail
        row = store.query("SELECT * FROM hourly WHERE hour = ?", (HOUR,))[0]

        self.assertAlmostEqual(row["wh"], 100.0, msg="existing data must survive")
        self.assertAlmostEqual(row["secs_missing"], 0.0)
        # And the migrated database still accepts writes.
        store.add_energy(HOUR, HOUR + 60, WATTS, busy=False, rate=RATE, complete=False)
        self.assertAlmostEqual(
            store.query("SELECT secs_missing FROM hourly WHERE hour = ?", (HOUR,))[0][0],
            60.0)


if __name__ == "__main__":
    unittest.main()
