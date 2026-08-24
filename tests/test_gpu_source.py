"""GpuReader source selection and NVML/subprocess parity.

Run from the repository root:  python3 -m unittest discover -s tests

NVML itself cannot be exercised without an NVIDIA driver, so these use a fake
that speaks the same interface. What they pin down is the wiring: that NVML is
preferred, that its readings reach the aggregate unchanged, that a failure is
reported as unknown rather than zero, and that a host without the library still
works through nvidia-smi.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powermon import GpuReader  # noqa: E402

CFG = {"gpu": {"enabled": True}}


class FakeNvml:
    """Stands in for the ctypes wrapper, with the same surface."""

    def __init__(self, devices=None, identity=None, procs=None, available=True):
        self._devices = devices
        self._identity = identity if identity is not None else [
            {"index": 0, "name": "NVIDIA GeForce RTX 3090", "limit_w": 350.0}]
        self._procs = procs or []
        self.available = available
        self.shutdown_calls = 0

    def identify(self):
        return self._identity

    def sample(self):
        return self._devices

    def processes(self, limit=6):
        return self._procs[:limit]

    def shutdown(self):
        self.shutdown_calls += 1


def reading(index=0, power=120.0, **overrides):
    d = {"index": index, "power_w": power, "util": 55.0, "mem_used": 2048.0,
         "mem_total": 24576.0, "temp": 61.0, "fan": 40.0, "clock_mhz": 1800.0}
    d.update(overrides)
    return d


class SourceSelectionTests(unittest.TestCase):
    def test_nvml_is_preferred_when_available(self):
        reader = GpuReader(CFG, nvml=FakeNvml(devices=[reading()]))
        self.assertEqual(reader.source, "nvml")
        self.assertTrue(reader.enabled)

    def test_falls_back_when_the_library_is_unavailable(self):
        """A host with nvidia-smi but no loadable NVML must still work."""
        reader = GpuReader(CFG, nvml=FakeNvml(available=False))
        self.assertEqual(reader.source, "nvidia-smi")

    def test_disabled_by_configuration(self):
        reader = GpuReader({"gpu": {"enabled": False}}, nvml=FakeNvml(devices=[reading()]))
        self.assertFalse(reader.enabled)
        self.assertEqual(reader.sample(), {"present": False})

    def test_close_shuts_the_library_down(self):
        fake = FakeNvml(devices=[reading()])
        GpuReader(CFG, nvml=fake).close()
        self.assertEqual(fake.shutdown_calls, 1)


class NvmlSamplingTests(unittest.TestCase):
    def test_readings_reach_the_aggregate(self):
        reader = GpuReader(CFG, nvml=FakeNvml(devices=[reading(power=133.5)]))
        sample = reader.sample()

        self.assertTrue(sample["present"])
        self.assertFalse(sample.get("error", False))
        self.assertAlmostEqual(sample["power_w"], 133.5)
        self.assertAlmostEqual(sample["temp"], 61.0)
        self.assertEqual(sample["count"], 1)
        self.assertEqual(sample["name"], "NVIDIA GeForce RTX 3090")
        self.assertAlmostEqual(sample["limit_w"], 350.0)

    def test_two_devices_sum(self):
        fake = FakeNvml(
            devices=[reading(0, 100.0), reading(1, 150.0)],
            identity=[{"index": 0, "name": "NVIDIA A100", "limit_w": 300.0},
                      {"index": 1, "name": "NVIDIA A100", "limit_w": 300.0}])
        sample = GpuReader(CFG, nvml=fake).sample()

        self.assertAlmostEqual(sample["power_w"], 250.0)
        self.assertAlmostEqual(sample["limit_w"], 600.0)
        self.assertEqual(sample["name"], "2 x NVIDIA A100")

    def test_a_total_library_failure_is_an_error_not_a_zero(self):
        reader = GpuReader(CFG, nvml=FakeNvml(devices=None))
        sample = reader.sample()

        self.assertTrue(sample["error"])
        self.assertNotIn("power_w", sample)
        self.assertEqual(reader.fail_count, 1)

    def test_an_unreadable_power_sensor_leaves_power_unknown(self):
        """The false-zero rule, through the NVML path."""
        sample = GpuReader(CFG, nvml=FakeNvml(devices=[reading(power=None)])).sample()

        self.assertIsNone(sample["power_w"])
        # Other fields still report, so the panel is not blanked.
        self.assertAlmostEqual(sample["temp"], 61.0)

    def test_per_device_identity_is_attached(self):
        fake = FakeNvml(
            devices=[reading(0, 100.0), reading(1, 100.0)],
            identity=[{"index": 0, "name": "NVIDIA A100", "limit_w": 300.0},
                      {"index": 1, "name": "NVIDIA H100", "limit_w": 700.0}])
        devices = GpuReader(CFG, nvml=fake).sample()["devices"]

        self.assertEqual([d["name"] for d in devices], ["NVIDIA A100", "NVIDIA H100"])
        self.assertEqual([d["limit_w"] for d in devices], [300.0, 700.0])

    def test_processes_are_passed_through(self):
        procs = [{"pid": "3478", "name": "VLLM::EngineCore", "mem_mib": 21830.0}]
        sample = GpuReader(CFG, nvml=FakeNvml(devices=[reading()], procs=procs)).sample()

        self.assertEqual(sample["procs"], procs)

    def test_no_processes_is_an_empty_list_not_a_failure(self):
        sample = GpuReader(CFG, nvml=FakeNvml(devices=[reading()], procs=[])).sample()

        self.assertEqual(sample["procs"], [])
        self.assertFalse(sample.get("error", False))


if __name__ == "__main__":
    unittest.main()
