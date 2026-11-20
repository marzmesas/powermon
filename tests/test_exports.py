"""CSV history export and the Prometheus text format.

Run from the repository root:  python3 -m unittest discover -s tests
"""
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powermon import history_csv, prometheus_text  # noqa: E402


class HistoryCsvTests(unittest.TestCase):
    def test_header_rows_and_empty_cells_for_none(self):
        series = {"t": [1000, 1300], "total": [10.0, 20.5], "gpu": [None, 3.0],
                  "cpu": [1.0, 2.0], "other": [0.5, 0.25], "kwh": [0.1, 0.2],
                  "gpu_temp": [None, 55.0], "cpu_temp": [40.0, None],
                  "busy_frac": [0.0, 1.0], "bucket_s": 300.0}
        lines = history_csv(series).splitlines()
        self.assertEqual(lines[0],
                         "time,total,gpu,cpu,other,kwh,gpu_temp,cpu_temp,busy_frac")
        self.assertEqual(len(lines), 3)
        first = lines[1].split(",")
        self.assertEqual(first[0], datetime.fromtimestamp(1000).isoformat(timespec="seconds"))
        self.assertEqual(first[2], "")      # gpu None -> empty, not 0
        self.assertEqual(first[6], "")      # gpu_temp None -> empty
        self.assertEqual(first[7], "40.0")
        self.assertEqual(lines[2].split(",")[7], "")

    def test_cost_column_only_when_present(self):
        series = {"t": [0], "total": [1.0], "kwh": [0.5], "cost": [0.07], "bucket_s": 3600.0}
        header = history_csv(series).splitlines()[0]
        self.assertEqual(header, "time,total,kwh,cost")

    def test_empty_series_is_header_only(self):
        self.assertEqual(history_csv({"t": [], "total": []}).splitlines(), ["time,total"])


def sample_payload(**overrides):
    payload = {
        "power": {"total_w": 350.0, "cpu_w": 80.0, "gpu_w": 240.0, "other_w": 30.0,
                  "cost_per_h": 0.0525},
        "cpu": {"pct": 12.5, "temp": None},
        "gpu": {"util": 90.0, "temp": 61.0, "mem_used": 8192.0},
        "mem": {"pct": 40.0},
        "disk": {"pct": None},
        "busy": True,
        "alert": {"active": False, "since": None},
        "totals": {"all": {"kwh": 12.5, "cost": 1.875, "idle_kwh": 2.0}},
        "health": {"last_sample_age_s": 0.8},
        "meta": {"rate": 0.15, "version": "0.1.0", "host": 'box"1'},
    }
    payload.update(overrides)
    return payload


class PrometheusTests(unittest.TestCase):
    def setUp(self):
        self.lines = prometheus_text(sample_payload()).splitlines()

    def test_expected_lines(self):
        expected = [
            '# HELP powermon_power_watts Power draw in watts, by component.',
            '# TYPE powermon_power_watts gauge',
            'powermon_power_watts{component="total"} 350.0',
            'powermon_power_watts{component="cpu"} 80.0',
            'powermon_power_watts{component="gpu"} 240.0',
            'powermon_power_watts{component="other"} 30.0',
            '# TYPE powermon_energy_kwh_total counter',
            'powermon_energy_kwh_total 12.5',
            'powermon_cost_total 1.875',
            'powermon_busy 1.0',
            'powermon_alert_active 0.0',
            'powermon_gpu_memory_used_mib 8192.0',
            'powermon_tariff_rate 0.15',
            'powermon_last_sample_age_seconds 0.8',
            'powermon_info{version="0.1.0",host="box\\"1"} 1.0',
        ]
        for line in expected:
            self.assertIn(line, self.lines)

    def test_none_values_are_omitted_not_zero(self):
        samples = [ln for ln in self.lines if not ln.startswith("#")]
        self.assertFalse(any(ln.startswith("powermon_cpu_temperature_celsius") for ln in samples))
        self.assertFalse(any(ln.startswith("powermon_disk_used_percent") for ln in samples))
        # The metric still gets its HELP/TYPE header; only the sample is dropped.
        self.assertIn("# TYPE powermon_disk_used_percent gauge", self.lines)

    def test_label_value_escaping(self):
        text = prometheus_text(sample_payload())
        self.assertIn('host="box\\"1"', text)
        self.assertTrue(text.endswith("\n"))


if __name__ == "__main__":
    unittest.main()
