"""Suspend-when-idle and default-route MAC lookup. Nothing here runs a real command.

Run from the repository root:  python3 -m unittest discover -s tests
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powermon import IdleSuspend, default_mac  # noqa: E402

WINDOW = 30 * 60


class FakeNotifier:
    host = "box"

    def __init__(self, log):
        self.log = log

    def push(self, kind, title, body, priority, tags):
        self.log.append(("push", kind, priority, body))


def make(after=30.0, logged_in=False, fail=False, **overrides):
    log = []
    cfg = {"suspend": {"after_idle_min": after, "command": "systemctl suspend",
                       "max_cpu_pct": 20.0, "skip_if_logged_in": True, **overrides}}

    def runner(command):
        log.append(("run", command))
        if fail:
            raise RuntimeError("denied")
    state = {"in": logged_in}
    s = IdleSuspend(cfg, FakeNotifier(log), lambda: state["in"], runner)
    return s, log, state


def feed(s, start, seconds, step=2, busy=False, cpu=1.0, run=False):
    t = start
    while t < start + seconds:
        s.observe(t, busy, cpu, run)
        t += step
    return t


def runs(log):
    return [e for e in log if e[0] == "run"]


class IdleSuspendTests(unittest.TestCase):
    def test_fires_once_after_window_notifying_first(self):
        s, log, _ = make()
        feed(s, 0, WINDOW - 10)
        self.assertEqual(log, [])                 # not within the first window
        feed(s, WINDOW - 10, 600)
        self.assertEqual([e[0] for e in log], ["push", "run"])
        self.assertEqual(log[0][1:3], ("suspend", "low"))
        self.assertIn("box going to sleep after 30 min idle", log[0][3])
        self.assertEqual(log[1], ("run", "systemctl suspend"))

    def test_each_blocker_resets_the_timer(self):
        for kw in ({"busy": True}, {"cpu": 50.0}, {"run": True}):
            s, log, _ = make()
            t = feed(s, 0, WINDOW - 10)
            t = feed(s, t, 4, **kw)
            feed(s, t, WINDOW - 20)
            self.assertEqual(log, [], kw)
            feed(s, t + WINDOW, 60)
            self.assertEqual(len(runs(log)), 1, kw)

    def test_logged_in_user_resets_and_can_be_ignored(self):
        s, log, who = make()
        t = feed(s, 0, WINDOW - 10)
        who["in"] = True
        t = feed(s, t, 4)
        who["in"] = False
        feed(s, t, WINDOW - 20)
        self.assertEqual(log, [])
        s, log, _ = make(logged_in=True, skip_if_logged_in=False)
        feed(s, 0, WINDOW + 10)
        self.assertEqual(len(runs(log)), 1)

    def test_disabled_at_zero(self):
        s, log, _ = make(after=0.0)
        feed(s, 0, 10 * 3600)
        self.assertEqual(log, [])
        self.assertFalse(s.enabled)
        self.assertIsNone(s.remaining(0))

    def test_gap_resets(self):
        s, log, _ = make()
        t = feed(s, 0, WINDOW - 10)
        feed(s, t + 3600, 20)                     # woke up an hour later
        self.assertEqual(log, [])
        self.assertAlmostEqual(s.remaining(t + 3600 + 20), WINDOW - 18, delta=2)

    def test_failure_does_not_retry_until_a_full_window_later(self):
        s, log, _ = make(fail=True)
        t = feed(s, 0, WINDOW + 10)
        self.assertEqual(len(runs(log)), 1)
        t = feed(s, t, WINDOW - 20)
        self.assertEqual(len(runs(log)), 1)
        feed(s, t, 60)
        self.assertEqual(len(runs(log)), 2)

    def test_after_firing_countdown_restarts(self):
        s, log, _ = make()
        t = feed(s, 0, WINDOW + 10)
        self.assertEqual(len(runs(log)), 1)
        self.assertAlmostEqual(s.remaining(t), WINDOW, delta=20)

    def test_remaining_counts_down(self):
        s, _, _ = make()
        s.observe(0, False, 1.0, False)
        self.assertEqual(s.remaining(100), WINDOW - 100)
        s.observe(2, True, 1.0, False)
        self.assertIsNone(s.remaining(3))

    def test_bad_config_is_rejected(self):
        with self.assertRaises(ValueError):
            make(after=-1.0)
        with self.assertRaises(ValueError):
            make(command=" ")
        make(after=0.0, command="")               # fine while disabled


ROUTE = ("Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
         "wlan0\t00000000\t0100A8C0\t0003\t0\t0\t600\t00000000\t0\t0\t0\n"
         "eth0\t00000000\t0100A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"
         "eth0\t0000A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0\n")


class DefaultMacTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.route, self.net = self.root / "route", self.root / "net"
        self.route.write_text(ROUTE)

    def nic(self, name, address):
        (self.net / name).mkdir(parents=True, exist_ok=True)
        if address is not None:
            (self.net / name / "address").write_text(address + "\n")

    def test_lowest_metric_default_route_wins(self):
        self.nic("wlan0", "11:22:33:44:55:66")
        self.nic("eth0", "AA:BB:CC:DD:EE:FF")
        self.assertEqual(default_mac(self.route, self.net), "aa:bb:cc:dd:ee:ff")

    def test_zero_or_missing_address_is_none(self):
        self.nic("eth0", "00:00:00:00:00:00")
        self.assertIsNone(default_mac(self.route, self.net))
        (self.net / "eth0" / "address").unlink()
        self.assertIsNone(default_mac(self.route, self.net))

    def test_no_default_route_or_no_table(self):
        self.route.write_text(ROUTE.splitlines()[0] + "\n" + ROUTE.splitlines()[3] + "\n")
        self.assertIsNone(default_mac(self.route, self.net))
        self.assertIsNone(default_mac(self.root / "nope", self.net))


if __name__ == "__main__":
    unittest.main()


class SendGraceTests(unittest.TestCase):
    def test_a_configured_push_gets_time_to_leave_before_the_suspend(self):
        from unittest import mock
        import powermon

        order = []
        notifier = type("N", (), {"host": "h", "ntfy": "https://ntfy.example/t", "webhook": "",
                                  "push": lambda self, *a: order.append("push")})()
        cfg = {"suspend": {"after_idle_min": 1, "command": "true", "max_cpu_pct": 20,
                           "skip_if_logged_in": False}}
        suspend = powermon.IdleSuspend(cfg, notifier, runner=lambda c: order.append("run"))
        with mock.patch.object(powermon.time, "sleep", lambda s: order.append(f"wait {s:g}")):
            for t in range(0, 70, 2):
                suspend.observe(float(t), False, 1.0, False)
        self.assertEqual(order, ["push", "wait 4", "run"])
