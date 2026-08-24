"""Health and data-quality reporting.

Run from the repository root:  python3 -m unittest discover -s tests

Phase 1 item 5 of the product audit: make the trustworthiness of the reading
visible rather than implicit. The three failure modes must stay distinguishable
because their fixes differ -- a stalled sampler, an unreadable sensor, and
history that was recorded with gaps are not the same problem.
"""
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powermon import health  # noqa: E402

CFG = {"sampling": {"interval": 2.0}}


class FakeCpu:
    def __init__(self, source="rapl", packages=1):
        self.source = source
        self.packages = packages


class FakeGpu:
    def __init__(self, enabled=True, source="nvml"):
        self.enabled = enabled
        self.source = source


class FakeMonitor:
    def __init__(self, age=1.0, gpu_error=False, cpu_source="rapl", count=1,
                 gpu_enabled=True):
        self.latest = {"ts": time.time() - age,
                       "gpu": {"error": gpu_error, "count": count}}
        self.cpu = FakeCpu(cpu_source)
        self.gpu = FakeGpu(gpu_enabled)


def totals(coverage=1.0):
    return {"today": {"coverage": coverage}}


def codes(result):
    return {i["code"] for i in result["issues"]}


class HealthTests(unittest.TestCase):
    def test_a_well_host_is_ok_and_quiet(self):
        result = health(FakeMonitor(), totals(), CFG)

        self.assertTrue(result["ok"])
        self.assertEqual(result["issues"], [])
        self.assertLess(result["last_sample_age_s"], 5)

    def test_a_stalled_sampler_is_an_error(self):
        result = health(FakeMonitor(age=120), totals(), CFG)

        self.assertFalse(result["ok"])
        self.assertIn("sampler_stalled", codes(result))

    def test_a_few_missed_ticks_is_not_yet_a_stall(self):
        """Otherwise every brief hiccup would cry wolf."""
        result = health(FakeMonitor(age=6), totals(), CFG)

        self.assertTrue(result["ok"])
        self.assertNotIn("sampler_stalled", codes(result))

    def test_an_unreadable_gpu_is_an_error_and_says_why_it_matters(self):
        result = health(FakeMonitor(gpu_error=True), totals(), CFG)

        self.assertFalse(result["ok"])
        self.assertIn("gpu_unreadable", codes(result))
        message = next(i["message"] for i in result["issues"]
                       if i["code"] == "gpu_unreadable")
        self.assertIn("missing from the total", message)

    def test_partial_coverage_warns_without_declaring_the_host_unwell(self):
        """The reading now is fine; it is the history that has holes."""
        result = health(FakeMonitor(), totals(coverage=0.82), CFG)

        self.assertTrue(result["ok"], "past gaps do not make the current reading wrong")
        self.assertIn("partial_coverage", codes(result))
        warning = next(i for i in result["issues"] if i["code"] == "partial_coverage")
        self.assertEqual(warning["level"], "warn")
        self.assertIn("18.0%", warning["message"])

    def test_full_coverage_is_not_reported_as_a_rounding_artefact(self):
        result = health(FakeMonitor(), totals(coverage=0.9999), CFG)
        self.assertNotIn("partial_coverage", codes(result))

    def test_estimated_cpu_is_information_not_a_fault(self):
        result = health(FakeMonitor(cpu_source="estimated"), totals(), CFG)

        self.assertTrue(result["ok"])
        note = next(i for i in result["issues"] if i["code"] == "cpu_estimated")
        self.assertEqual(note["level"], "info")

    def test_measured_cpu_says_nothing(self):
        self.assertNotIn("cpu_estimated", codes(health(FakeMonitor(), totals(), CFG)))

    def test_the_three_failures_stay_separable(self):
        result = health(FakeMonitor(age=120, gpu_error=True, cpu_source="estimated"),
                        totals(coverage=0.5), CFG)

        self.assertEqual(codes(result),
                         {"sampler_stalled", "gpu_unreadable",
                          "partial_coverage", "cpu_estimated"})
        self.assertFalse(result["ok"])

    def test_sources_are_reported_for_display(self):
        result = health(FakeMonitor(count=4), totals(), CFG)

        self.assertEqual(result["cpu_source"], "rapl")
        self.assertEqual(result["gpu_source"], "nvml")
        self.assertEqual(result["gpu_count"], 4)
        self.assertEqual(result["cpu_packages"], 1)

    def test_a_gpuless_host_reports_no_gpu_source(self):
        monitor = FakeMonitor(gpu_enabled=False, count=0)
        self.assertIsNone(health(monitor, totals(), CFG)["gpu_source"])

    def test_a_host_that_has_never_sampled_is_not_ok(self):
        monitor = FakeMonitor()
        monitor.latest = {}
        self.assertFalse(health(monitor, totals(), CFG)["ok"])


if __name__ == "__main__":
    unittest.main()
