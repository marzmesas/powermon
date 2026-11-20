"""Training runs: the busy/idle state machine and the API shaping.

Run from the repository root:  python3 -m unittest discover -s tests
"""
import csv
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powermon import RUN_FIELDS, RunTracker, Store, runs_csv, runs_list, shape_run  # noqa: E402

CFG = {"activity": {"run_min_s": 60, "run_gap_s": 120}}
PROC = [{"pid": 1, "name": "train.py", "mem_mib": 100}]


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "p.db"
        self.store = Store(self.path)
        self.tr = RunTracker(CFG, self.store)
        self.t = 1000.0
        self.tr.observe(self.t, False, None, None, None, [])   # first sample: no interval

    def feed(self, seconds, busy, watts=360.0, rate=0.30, known=True, procs=PROC, step=2):
        end = self.t + seconds
        while self.t < end:
            self.t += step
            wh = watts * step / 3600.0 if known else None
            self.tr.observe(self.t, busy, wh, wh / 1000.0 * rate if known else None,
                            watts if known else None, procs if busy else [])

    def runs(self):
        return runs_list(self.store, now=self.t)

    def test_blip_is_not_a_run(self):
        self.feed(40, True)
        self.feed(600, False)
        self.assertEqual(self.runs(), [])

    def test_short_pause_does_not_split_and_end_is_when_it_went_idle(self):
        self.feed(100, True)
        self.feed(60, False)                  # shorter than the gap
        self.feed(100, True)
        busy_end = self.t
        self.feed(300, False)
        (run,) = self.runs()
        self.assertFalse(run["running"])
        self.assertEqual(run["end"], busy_end)
        self.assertEqual(run["start"], 1000.0)
        self.assertEqual(run["name"], "train.py")

    def test_long_pause_splits(self):
        self.feed(100, True)
        self.feed(200, False)
        self.feed(100, True)
        self.assertEqual([r["running"] for r in self.runs()], [True, False])

    def test_energy_and_cost_follow_the_changing_rate(self):
        self.feed(100, True, watts=360, rate=0.10)   # 10 Wh
        self.feed(100, True, watts=720, rate=0.20)   # 20 Wh
        self.feed(500, False)                        # closing gap is not charged
        (run,) = self.runs()
        self.assertAlmostEqual(run["kwh"], 0.030)
        self.assertAlmostEqual(run["cost"], 0.010 * 0.10 + 0.020 * 0.20)
        self.assertEqual(run["peak_w"], 720)
        self.assertAlmostEqual(run["avg_w"], 540)

    def test_unknown_gpu_interval_adds_nothing(self):
        self.feed(100, True, watts=360)
        self.feed(20, True, known=False)
        self.feed(80, True, watts=360)
        self.feed(500, False)
        (run,) = self.runs()
        self.assertAlmostEqual(run["kwh"], 360 * 180 / 3600 / 1000)
        self.assertAlmostEqual(run["avg_w"], 360)   # not diluted by the gap

    def test_open_run_is_closed_on_restart(self):
        self.feed(100, True)
        self.feed(40, True)                         # flushed at most ~30 s ago
        self.assertTrue(self.runs()[0]["running"])
        RunTracker(CFG, Store(self.path))
        (run,) = self.runs()
        self.assertFalse(run["running"])
        self.assertLessEqual(run["end"], self.t)
        self.assertGreater(run["end"], self.t - 31)

    def test_current_is_none_until_it_is_a_run(self):
        self.feed(30, True)
        self.assertIsNone(self.tr.current())
        self.feed(60, True)
        self.assertTrue(self.tr.current()["running"])


class ShapeTests(unittest.TestCase):
    ROW = {"id": 3, "start_ts": 100.0, "end_ts": None, "wh": 500.0, "cost": 0.15,
           "peak_w": None, "secs_known": 0.0, "name": None}

    def test_running_and_unknowns(self):
        r = shape_run(self.ROW, 400.0)
        self.assertTrue(r["running"])
        self.assertEqual(r["seconds"], 300.0)
        self.assertIsNone(r["avg_w"])
        self.assertIsNone(r["peak_w"])

    def test_csv_has_header_and_blanks_for_unknown(self):
        done = dict(self.ROW, end_ts=200.0, secs_known=100.0, name="a,b")
        rows = list(csv.reader(io.StringIO(runs_csv([shape_run(done, 0), shape_run(self.ROW, 400)]))))
        self.assertEqual(rows[0], RUN_FIELDS)
        self.assertEqual(rows[1][RUN_FIELDS.index("name")], "a,b")
        self.assertEqual(rows[2][RUN_FIELDS.index("avg_w")], "")
        self.assertEqual(rows[2][RUN_FIELDS.index("end")], "")

    def test_run_co2_from_its_kwh_and_the_factor(self):
        self.assertAlmostEqual(shape_run(self.ROW, 400.0, 400.0)["co2_kg"], 0.5 * 400.0 / 1000.0)
        self.assertIsNone(shape_run(self.ROW, 400.0)["co2_kg"])
        self.assertIsNone(shape_run(self.ROW, 400.0, None)["co2_kg"])

    def test_csv_co2_column_blank_when_not_configured(self):
        done = dict(self.ROW, end_ts=200.0, secs_known=100.0)
        off = list(csv.reader(io.StringIO(runs_csv([shape_run(done, 0)]))))
        on = list(csv.reader(io.StringIO(runs_csv([shape_run(done, 0, 400.0)]))))
        self.assertEqual(off[1][RUN_FIELDS.index("co2_kg")], "")
        self.assertAlmostEqual(float(on[1][RUN_FIELDS.index("co2_kg")]), 0.2)


if __name__ == "__main__":
    unittest.main()
