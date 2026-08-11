"""Multi-device regression tests: several GPUs, several CPU packages.

Run from the repository root:  python3 -m unittest discover -s tests

The product audit's P1: nvidia-smi was asked about every GPU but only row 0
was read, and RAPL discovery kept the first package it found. Both produce a
plausible-looking number that is short by whole devices.
"""
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powermon import CpuReader, aggregate_gpus  # noqa: E402


def device(index, power, name="NVIDIA GeForce RTX 3090", **overrides):
    d = {"index": index, "name": name, "power_w": power, "util": 10.0,
         "mem_used": 1024.0, "mem_total": 24576.0, "temp": 40.0, "fan": 30.0,
         "clock_mhz": 210.0, "limit_w": 350.0}
    d.update(overrides)
    return d


class GpuAggregationTests(unittest.TestCase):
    def test_two_gpus_sum_to_the_host_total(self):
        """The audit's acceptance criterion, stated literally."""
        agg = aggregate_gpus([device(0, 100.0), device(1, 150.0)])
        self.assertAlmostEqual(agg["power_w"], 250.0)

    def test_single_gpu_is_unchanged(self):
        agg = aggregate_gpus([device(0, 104.5)])
        self.assertAlmostEqual(agg["power_w"], 104.5)
        self.assertEqual(agg["name"], "NVIDIA GeForce RTX 3090")
        self.assertEqual(agg["count"], 1)

    def test_no_devices(self):
        agg = aggregate_gpus([])
        self.assertIsNone(agg["power_w"])
        self.assertEqual(agg["count"], 0)

    def test_eight_gpus(self):
        agg = aggregate_gpus([device(i, 300.0) for i in range(8)])
        self.assertAlmostEqual(agg["power_w"], 2400.0)
        self.assertAlmostEqual(agg["limit_w"], 2800.0)
        self.assertEqual(agg["count"], 8)

    def test_one_unreadable_device_makes_the_total_unknown_not_short(self):
        """A partial failure must not silently drop a card from the total."""
        agg = aggregate_gpus([device(0, 100.0), device(1, None)])
        self.assertIsNone(agg["power_w"], "a 100 W total here would understate by a card")
        # The rest still reports, so the UI is not blanked.
        self.assertAlmostEqual(agg["mem_used"], 2048.0)
        self.assertEqual(agg["count"], 2)

    def test_memory_and_limits_sum(self):
        agg = aggregate_gpus([device(0, 100.0, mem_used=1000.0, mem_total=24576.0),
                              device(1, 100.0, mem_used=500.0, mem_total=12288.0,
                                     limit_w=250.0)])
        self.assertAlmostEqual(agg["mem_used"], 1500.0)
        self.assertAlmostEqual(agg["mem_total"], 36864.0)
        self.assertAlmostEqual(agg["limit_w"], 600.0)

    def test_temperature_and_fan_report_the_worst_device(self):
        agg = aggregate_gpus([device(0, 100.0, temp=45.0, fan=20.0, util=5.0),
                              device(1, 100.0, temp=83.0, fan=95.0, util=99.0)])
        self.assertAlmostEqual(agg["temp"], 83.0)
        self.assertAlmostEqual(agg["fan"], 95.0)
        self.assertAlmostEqual(agg["util"], 99.0, msg="a busy card must not be averaged away")

    def test_partial_memory_still_sums_what_is_known(self):
        agg = aggregate_gpus([device(0, 100.0, mem_used=1000.0),
                              device(1, 100.0, mem_used=None)])
        self.assertAlmostEqual(agg["mem_used"], 1000.0)

    def test_names_describe_the_set(self):
        same = aggregate_gpus([device(0, 1.0), device(1, 1.0)])
        self.assertEqual(same["name"], "2 x NVIDIA GeForce RTX 3090")

        mixed = aggregate_gpus([device(0, 1.0), device(1, 1.0, name="NVIDIA A100")])
        self.assertEqual(mixed["name"], "2 GPUs")


class RaplPackageTests(unittest.TestCase):
    """A fake sysfs tree, so multi-socket behaviour is testable off a server."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = {"cpu": {"rapl_scale": 1.0, "idle_w": 30.0, "max_w": 142.0,
                            "curve_exp": 1.25}}

    def add_package(self, index, microjoules, name=None):
        dom = self.root / f"intel-rapl:{index}"
        dom.mkdir()
        (dom / "name").write_text(name or f"package-{index}")
        (dom / "energy_uj").write_text(str(microjoules))
        (dom / "max_energy_range_uj").write_text(str(2 ** 32))
        return dom

    def advance(self, dom, microjoules):
        (dom / "energy_uj").write_text(str(microjoules))

    def test_both_sockets_are_found_and_summed(self):
        a = self.add_package(0, 0)
        b = self.add_package(1, 0)
        reader = CpuReader(self.cfg, rapl_root=self.root)
        self.assertEqual(reader.packages, 2)
        self.assertEqual(reader.source, "rapl")

        now = time.time()
        reader.power(0.0, now)                    # priming read
        self.advance(a, 40_000_000)               # 40 J in 1 s -> 40 W
        self.advance(b, 60_000_000)               # 60 J in 1 s -> 60 W
        watts = reader.power(0.0, now + 1.0)

        self.assertAlmostEqual(watts, 100.0, places=3,
                               msg="one socket alone would report 40 W")

    def test_single_package_is_unchanged(self):
        a = self.add_package(0, 0)
        reader = CpuReader(self.cfg, rapl_root=self.root)
        self.assertEqual(reader.packages, 1)

        now = time.time()
        reader.power(0.0, now)
        self.advance(a, 55_000_000)
        self.assertAlmostEqual(reader.power(0.0, now + 1.0), 55.0, places=3)

    def test_non_package_domains_are_ignored(self):
        self.add_package(0, 0)
        self.add_package(1, 0, name="core")       # a sub-domain, not a socket
        reader = CpuReader(self.cfg, rapl_root=self.root)
        self.assertEqual(reader.packages, 1)

    def test_no_rapl_falls_back_to_the_model(self):
        reader = CpuReader(self.cfg, rapl_root=self.root)
        self.assertEqual(reader.packages, 0)
        self.assertEqual(reader.source, "estimated")
        self.assertGreater(reader.power(50.0, time.time()), 0.0)

    def test_counter_wrap_is_handled_per_package(self):
        a = self.add_package(0, 2 ** 32 - 10_000_000)
        b = self.add_package(1, 0)
        reader = CpuReader(self.cfg, rapl_root=self.root)

        now = time.time()
        reader.power(0.0, now)
        self.advance(a, 10_000_000)               # wrapped: 20 J across the boundary
        self.advance(b, 30_000_000)
        watts = reader.power(0.0, now + 1.0)

        self.assertAlmostEqual(watts, 50.0, places=3)


if __name__ == "__main__":
    unittest.main()
