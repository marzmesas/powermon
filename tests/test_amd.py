"""AMD GPU support, read from a fake sysfs tree.

Run from the repository root:  python3 -m unittest discover -s tests

No AMD hardware was available, so the tree below is the only check of the
file formats: microwatts, millidegrees, bytes and Hz as the amdgpu driver
documents them.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powermon import AmdSysfs, GpuReader, aggregate_gpus  # noqa: E402
from test_gpu_source import CFG, FakeNvml, reading  # noqa: E402


def put(path: Path, text) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{text}\n")


def make_card(root: Path, name="card0", vendor="0x1002", hw=None, files=None):
    dev = root / name / "device"
    put(dev / "vendor", vendor)
    for fname, value in (files if files is not None else {
            "gpu_busy_percent": 42, "mem_info_vram_used": 2 * 1048576,
            "mem_info_vram_total": 16384 * 1048576, "product_name": "Radeon RX 7900 XTX",
            "device": "0x744c"}).items():
        put(dev / fname, value)
    for fname, value in (hw if hw is not None else {
            "power1_average": 123_500_000, "temp1_input": 65_500, "pwm1": 128,
            "freq1_input": 2_300_000_000, "power1_cap": 300_000_000}).items():
        put(dev / "hwmon" / "hwmon3" / fname, value)
    return dev


class AmdSysfsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_discovery_skips_connectors_and_other_vendors(self):
        make_card(self.root, "card0")
        make_card(self.root, "card1", vendor="0x10de")
        make_card(self.root, "card0-DP-1")
        put(self.root / "version", "x")
        amd = AmdSysfs(self.root)
        self.assertTrue(amd.available)
        self.assertEqual([d["index"] for d in amd.sample()], [0])

    def test_missing_root_is_unavailable(self):
        self.assertFalse(AmdSysfs(self.root / "nope").available)

    def test_unit_conversions(self):
        make_card(self.root)
        d = AmdSysfs(self.root).sample()[0]
        self.assertEqual(d["power_w"], 123.5)
        self.assertEqual(d["util"], 42.0)
        self.assertEqual(d["mem_used"], 2.0)
        self.assertEqual(d["mem_total"], 16384.0)
        self.assertEqual(d["temp"], 65.5)
        self.assertAlmostEqual(d["fan"], 128 / 255 * 100)
        self.assertEqual(d["clock_mhz"], 2300.0)
        self.assertEqual(d["limit_w"], 300.0)
        self.assertEqual(d["name"], "Radeon RX 7900 XTX")

    def test_pwm_uses_pwm1_max_when_present(self):
        make_card(self.root, hw={"power1_average": 1_000_000, "pwm1": 100, "pwm1_max": 200})
        self.assertEqual(AmdSysfs(self.root).sample()[0]["fan"], 50.0)

    def test_power1_input_fallback(self):
        make_card(self.root, hw={"power1_input": 77_000_000})
        self.assertEqual(AmdSysfs(self.root).sample()[0]["power_w"], 77.0)

    def test_missing_files_are_none_not_zero(self):
        make_card(self.root, hw={})
        d = AmdSysfs(self.root).sample()[0]
        for key in ("power_w", "temp", "fan", "clock_mhz", "limit_w"):
            self.assertIsNone(d[key], key)
        self.assertEqual(d["mem_total"], 16384.0)

    def test_unreadable_power_makes_the_total_unknown(self):
        make_card(self.root, "card0")
        make_card(self.root, "card1", hw={})
        devices = AmdSysfs(self.root).sample()
        self.assertIsNone(devices[1]["power_w"])
        self.assertIsNone(aggregate_gpus(devices)["power_w"])

    def test_card_with_neither_power_nor_vram_is_skipped(self):
        make_card(self.root, "card0", hw={}, files={"gpu_busy_percent": 1})
        self.assertFalse(AmdSysfs(self.root).available)

    def test_vram_alone_is_enough(self):
        make_card(self.root, hw={})
        self.assertTrue(AmdSysfs(self.root).available)

    def test_name_fallback(self):
        make_card(self.root, files={"mem_info_vram_total": 1048576, "device": "0x73bf"})
        self.assertEqual(AmdSysfs(self.root).identify()[0]["name"], "AMD GPU 0x73bf")


class MixedHostTests(unittest.TestCase):
    def test_nvidia_and_amd_are_listed_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_card(Path(tmp))
            reader = GpuReader(CFG, nvml=FakeNvml(devices=[reading(0, power=100.0)]),
                               amd=AmdSysfs(Path(tmp)))
            sample = reader.sample()
        self.assertEqual(reader.source, "nvml+amdgpu")
        self.assertEqual([d["index"] for d in sample["devices"]], [0, 1])
        self.assertEqual(sample["count"], 2)
        self.assertEqual(sample["power_w"], 223.5)
        self.assertEqual(sample["devices"][1]["name"], "Radeon RX 7900 XTX")

    def test_amd_only_host(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("powermon.shutil.which", return_value=None):
            make_card(Path(tmp))
            reader = GpuReader(CFG, nvml=FakeNvml(available=False), amd=AmdSysfs(Path(tmp)))
            sample = reader.sample()
        self.assertEqual(reader.source, "amdgpu")
        self.assertEqual(sample["procs"], [])
        self.assertEqual(sample["devices"][0]["index"], 0)

    def test_unreadable_amd_power_makes_host_total_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_card(Path(tmp), hw={})
            reader = GpuReader(CFG, nvml=FakeNvml(devices=[reading(0)]), amd=AmdSysfs(Path(tmp)))
            self.assertIsNone(reader.sample()["power_w"])

    def test_disabled_by_configuration_ignores_amd(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_card(Path(tmp))
            reader = GpuReader({"gpu": {"enabled": False}}, amd=AmdSysfs(Path(tmp)))
            self.assertEqual(reader.sample(), {"present": False})


if __name__ == "__main__":
    unittest.main()
