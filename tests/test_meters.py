"""Wall meter providers.

Run from the repository root:  python3 -m unittest discover -s tests

Phase 2 item 1 of the product audit, and the fix for its accuracy finding: a
real meter makes the headline number a measurement instead of an estimate, and
turns baseline_w and psu_efficiency from assumptions into a reportable
residual.

The payload shapes below are the real ones each device emits, so the JSON paths
in the README are pinned by tests rather than by hope.
"""
import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powermon import (  # noqa: E402
    HttpMeter,
    MeterPoller,
    NutMeter,
    build_meter,
    dig,
)


class DigTests(unittest.TestCase):
    def test_nested_keys_and_list_indices(self):
        data = {"a": {"b": [{"c": 42}]}}
        self.assertEqual(dig(data, "a.b.0.c"), 42)

    def test_missing_paths_are_none_not_an_error(self):
        for path in ("x", "a.x", "a.b.9.c", "a.b.0.c.d"):
            self.assertIsNone(dig({"a": {"b": [{"c": 42}]}}, path), path)

    def test_empty_path_returns_the_whole_document(self):
        self.assertEqual(dig({"a": 1}, ""), {"a": 1})


def fake_http(payload, expect=None, fail=None):
    """Stands in for urllib.request.urlopen."""
    seen = {}

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def opener(request, timeout=None):
        seen["url"] = request.full_url
        seen["headers"] = dict(request.headers)
        seen["timeout"] = timeout
        if fail:
            raise fail
        return Response(json.dumps(payload).encode())

    opener.seen = seen
    return opener


class HttpMeterTests(unittest.TestCase):
    """The real response shapes from each supported device."""

    def test_tasmota(self):
        payload = {"StatusSNS": {"ENERGY": {"Power": 137.5, "Voltage": 231}}}
        meter = HttpMeter("http://plug/cm?cmnd=Status%2010",
                          "StatusSNS.ENERGY.Power", opener=fake_http(payload))
        self.assertAlmostEqual(meter.read(), 137.5)

    def test_shelly_gen1(self):
        payload = {"meters": [{"power": 88.2, "is_valid": True}]}
        meter = HttpMeter("http://plug/status", "meters.0.power",
                          opener=fake_http(payload))
        self.assertAlmostEqual(meter.read(), 88.2)

    def test_shelly_gen2(self):
        payload = {"id": 0, "apower": 240.1, "voltage": 229.8}
        meter = HttpMeter("http://plug/rpc/Switch.GetStatus?id=0", "apower",
                          opener=fake_http(payload))
        self.assertAlmostEqual(meter.read(), 240.1)

    def test_home_assistant_reports_a_string_state(self):
        payload = {"entity_id": "sensor.rack_power", "state": "412.7"}
        opener = fake_http(payload)
        meter = HttpMeter("http://ha:8123/api/states/sensor.rack_power", "state",
                          headers="Authorization: Bearer abc123", opener=opener)

        self.assertAlmostEqual(meter.read(), 412.7)
        self.assertEqual(opener.seen["headers"].get("Authorization"), "Bearer abc123")

    def test_several_headers_separated_by_pipes(self):
        opener = fake_http({"w": 1})
        HttpMeter("http://x", "w", headers="A: 1|B: 2", opener=opener).read()
        self.assertEqual(opener.seen["headers"].get("A"), "1")
        self.assertEqual(opener.seen["headers"].get("B"), "2")

    def test_scale_converts_kilowatts(self):
        meter = HttpMeter("http://x", "kw", scale=1000.0, opener=fake_http({"kw": 0.35}))
        self.assertAlmostEqual(meter.read(), 350.0)

    def test_an_unreachable_meter_reads_none_not_zero(self):
        meter = HttpMeter("http://x", "w", opener=fake_http(None, fail=OSError("down")))
        self.assertIsNone(meter.read())

    def test_a_wrong_path_reads_none(self):
        meter = HttpMeter("http://x", "not.here", opener=fake_http({"w": 5}))
        self.assertIsNone(meter.read())

    def test_malformed_json_reads_none(self):
        class Bad(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *e): return False

        def opener(request, timeout=None):
            return Bad(b"{ not json")

        self.assertIsNone(HttpMeter("http://x", "w", opener=opener).read())


class FakeSocket:
    """A NUT server that answers GET VAR from a dict."""

    def __init__(self, variables, fail=None):
        self.variables = variables
        self.fail = fail
        self.sent = []

    def __enter__(self):
        if self.fail:
            raise self.fail
        return self

    def __exit__(self, *exc):
        return False

    def settimeout(self, _):
        pass

    def makefile(self, *a, **kw):
        sock = self

        class File:
            def write(self, line):
                sock.sent.append(line.strip())

            def flush(self):
                pass

            def readline(self):
                last = sock.sent[-1]
                if not last.startswith("GET VAR "):
                    return "OK\n"
                var = last.split()[-1]
                if var not in sock.variables:
                    return f"ERR VAR-NOT-SUPPORTED\n"
                return f'VAR ups {var} "{sock.variables[var]}"\n'

        return File()


class NutMeterTests(unittest.TestCase):
    def test_realpower_is_preferred(self):
        sock = FakeSocket({"ups.realpower": "212.5"})
        meter = NutMeter(connect=lambda: sock)
        self.assertAlmostEqual(meter.read(), 212.5)

    def test_falls_back_to_load_times_nominal(self):
        """Most consumer UPSs do not report realpower at all."""
        sock = FakeSocket({"ups.load": "35", "ups.realpower.nominal": "600"})
        self.assertAlmostEqual(NutMeter(connect=lambda: sock).read(), 210.0)

    def test_a_ups_reporting_neither_reads_none(self):
        sock = FakeSocket({"battery.charge": "100"})
        self.assertIsNone(NutMeter(connect=lambda: sock).read())

    def test_load_without_a_nominal_rating_is_not_guessed(self):
        sock = FakeSocket({"ups.load": "35"})
        self.assertIsNone(NutMeter(connect=lambda: sock).read())

    def test_an_unreachable_ups_reads_none(self):
        meter = NutMeter(connect=lambda: FakeSocket({}, fail=OSError("refused")))
        self.assertIsNone(meter.read())

    def test_it_logs_out_rather_than_dropping_the_connection(self):
        sock = FakeSocket({"ups.realpower": "100"})
        NutMeter(connect=lambda: sock).read()
        self.assertIn("LOGOUT", sock.sent)

    def test_a_custom_variable_name_is_used(self):
        sock = FakeSocket({"outlet.1.power": "77"})
        meter = NutMeter(var="outlet.1.power", connect=lambda: sock)
        self.assertAlmostEqual(meter.read(), 77.0)


class BuildMeterTests(unittest.TestCase):
    def test_none_by_default(self):
        self.assertIsNone(build_meter({"meter": {"type": "none"}}))
        self.assertIsNone(build_meter({}))

    def test_http_needs_a_url(self):
        self.assertIsNone(build_meter({"meter": {"type": "http", "http_url": ""}}))
        meter = build_meter({"meter": {"type": "http", "http_url": "http://x",
                                       "http_json_path": "w"}})
        self.assertIsInstance(meter, HttpMeter)

    def test_nut_uses_its_defaults(self):
        meter = build_meter({"meter": {"type": "nut"}})
        self.assertIsInstance(meter, NutMeter)
        self.assertEqual(meter.port, 3493)
        self.assertEqual(meter.var, "ups.realpower")

    def test_an_unknown_type_is_ignored_rather_than_fatal(self):
        self.assertIsNone(build_meter({"meter": {"type": "smoke-signals"}}))


class MeterPollerTests(unittest.TestCase):
    class Flaky:
        def __init__(self, values):
            self.values = list(values)
            self.calls = 0

        def read(self):
            self.calls += 1
            return self.values.pop(0) if self.values else None

    def test_no_meter_configured_reads_none_without_calling_anything(self):
        self.assertIsNone(MeterPoller(None).read())

    def test_a_good_reading_passes_through(self):
        self.assertAlmostEqual(MeterPoller(self.Flaky([123.0])).read(), 123.0)

    def test_a_negative_reading_is_rejected(self):
        self.assertIsNone(MeterPoller(self.Flaky([-5.0])).read())

    def test_a_dead_meter_stops_being_polled_every_sample(self):
        """Otherwise a switched-off plug costs a timeout twice a second."""
        flaky = self.Flaky([])
        poller = MeterPoller(flaky, max_failures=3, retry_every=10)
        for _ in range(3):
            poller.read()
        self.assertEqual(flaky.calls, 3)

        for _ in range(10):
            poller.read()
        self.assertEqual(flaky.calls, 3, "kept polling a meter known to be down")

        poller.read()
        self.assertEqual(flaky.calls, 4, "never retried the meter")

    def test_recovery_resets_the_failure_count(self):
        poller = MeterPoller(self.Flaky([None, None, 90.0]), max_failures=5)
        poller.read()
        poller.read()
        self.assertAlmostEqual(poller.read(), 90.0)
        self.assertEqual(poller.failures, 0)
        self.assertFalse(poller.last_error)


if __name__ == "__main__":
    unittest.main()
