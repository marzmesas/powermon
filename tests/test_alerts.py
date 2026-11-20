"""Idle-draw alert: one push per episode, an urgent reminder, one all-clear.

Run from the repository root:  python3 -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tempfile  # noqa: E402
from datetime import datetime  # noqa: E402

from powermon import (BudgetAlert, IdleAlert, Notifier, Store,  # noqa: E402
                      ThresholdAlert)


def make(**overrides):
    alerts = {"idle_w": 130, "after_min": 10, "remind_min": 60,
              "ntfy_url": "https://ntfy.example/topic", **overrides}
    sent = []
    cfg = {"alerts": alerts}
    notifier = Notifier(cfg, send=lambda url, title, body, priority, tags:
                        sent.append((title, priority)))
    return IdleAlert(cfg, notifier), sent


def feed(alert, start, seconds, watts, busy=False, step=2):
    """Samples every `step` s for `seconds`; returns (end time, last state)."""
    state, t = None, start
    while t < start + seconds:
        state = alert.observe(t, watts, busy)
        t += step
    return t, state


class IdleAlertTests(unittest.TestCase):
    def test_this_mornings_episode(self):
        # 103 W idle, then 174 W with the GPU idle for three hours, then back.
        alert, sent = make()
        t, state = feed(alert, 0, 600, 103)
        self.assertEqual((sent, state["active"]), ([], False))
        t, state = feed(alert, t, 3 * 3600, 174)
        self.assertTrue(state["active"])
        self.assertEqual([p for _, p in sent], ["default", "urgent"])
        t, state = feed(alert, t, 120, 103)
        self.assertFalse(state["active"])
        self.assertEqual([p for _, p in sent], ["default", "urgent", "low"])
        self.assertIn("back to 103 W", sent[-1][0])

    def test_short_spike_is_ignored(self):
        alert, sent = make()
        t, _ = feed(alert, 0, 300, 205)
        feed(alert, t, 600, 103)
        self.assertEqual(sent, [])

    def test_training_is_not_an_alert(self):
        alert, sent = make()
        feed(alert, 0, 7200, 420, busy=True)
        self.assertEqual(sent, [])

    def test_brief_dip_does_not_restart_the_clock_or_clear(self):
        alert, sent = make()
        t, _ = feed(alert, 0, 500, 174)
        t, _ = feed(alert, t, 20, 103)          # shorter than CLEAR_S
        t, state = feed(alert, t, 200, 174)
        self.assertTrue(state["active"])
        self.assertEqual(len(sent), 1)

    def test_off_by_default_and_bad_url_never_sends(self):
        alert, sent = make(idle_w=0)
        feed(alert, 0, 7200, 500)
        self.assertEqual(sent, [])
        alert, sent = make(ntfy_url="file:///etc/passwd")
        _, state = feed(alert, 0, 7200, 500)
        self.assertEqual(sent, [])
        self.assertTrue(state["active"])        # still shown in /api/now

    def test_unknown_wall_power_is_not_over(self):
        alert, sent = make()
        feed(alert, 0, 7200, None)
        self.assertEqual(sent, [])



class TopProcessTests(unittest.TestCase):
    def test_names_the_process_that_used_the_most_cpu_in_the_window(self):
        import os
        import tempfile
        from powermon import top_cpu_process

        def stat(pid, name, ticks):
            folder = Path(tmp) / str(pid)
            folder.mkdir(exist_ok=True)
            # A name with spaces and brackets, as the kernel really writes them.
            (folder / "stat").write_text(
                f"{pid} ({name}) S 1 1 1 0 -1 0 0 0 0 0 {ticks} 0 0 0 20 0 1 0 0 0 0\n")

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "meminfo").write_text("")          # non-pid entries are skipped
            stat(10, "old hog", 900_000)                     # busy long ago, quiet now
            stat(20, "gnome-shell (x)", 100)

            def spin(_):
                stat(20, "gnome-shell (x)", 100 + os.sysconf("SC_CLK_TCK") // 2)

            text = top_cpu_process(window=1.0, proc=Path(tmp), sleep=spin)
        self.assertEqual(text, "Busiest process: gnome-shell (x), 50 % of a core.")

    def test_detail_is_only_computed_when_a_push_goes_out(self):
        alert, sent = make()
        calls = []
        for t in range(0, 1200, 2):
            alert.observe(t, 174, False, lambda: calls.append(t) or "")
        self.assertEqual((len(sent), len(calls)), (1, 1))


def notifier(**overrides):
    """A Notifier recording what each channel receives."""
    ntfy, hook = [], []
    cfg = {"alerts": {"ntfy_url": "https://ntfy.example/t", "webhook_url": "", **overrides}}
    n = Notifier(cfg, send=lambda url, title, body, prio, tags: ntfy.append((url, title, prio)),
                 post=lambda url, payload: hook.append((url, payload)))
    return n, ntfy, hook


class NotifierTests(unittest.TestCase):
    def test_webhook_payload_shape(self):
        n, ntfy, hook = notifier(webhook_url="https://hooks.example/x")
        n.push("disk", "box disk 91 %", " full ", "default", "warning")
        (url, payload), = hook
        self.assertEqual(url, "https://hooks.example/x")
        self.assertEqual(payload, {"title": "powermon: box disk 91 %", "message": "full",
                                   "priority": "default", "tags": "warning",
                                   "host": n.host, "kind": "disk"})

    def test_both_channels_receive_a_push(self):
        n, ntfy, hook = notifier(webhook_url="https://hooks.example/x")
        n.push("idle", "t", "b", "urgent", "x")
        self.assertEqual((len(ntfy), len(hook)), (1, 1))

    def test_webhook_only_and_non_http_refused(self):
        n, ntfy, hook = notifier(ntfy_url="", webhook_url="https://hooks.example/x")
        n.push("idle", "t", "b", "low", "x")
        self.assertEqual((len(ntfy), len(hook)), (0, 1))
        n, ntfy, hook = notifier(ntfy_url="file:///etc/passwd", webhook_url="ftp://h/x")
        n.push("idle", "t", "b", "low", "x")
        self.assertEqual((ntfy, hook), ([], []))

    def test_webhook_failure_is_dropped(self):
        from unittest import mock
        import powermon
        import time
        with mock.patch.object(powermon.urllib.request, "urlopen", side_effect=OSError("down")):
            powermon.webhook_send("https://hooks.example/x", {"a": 1})   # must not raise
            time.sleep(0.1)


def threshold(limit=80, after_s=300):
    n, ntfy, hook = notifier()
    return ThresholdAlert("gpu_temp", "GPU temperature", " C", limit, after_s, n), ntfy


def run(alert, start, seconds, value, step=2):
    t, state = start, None
    while t < start + seconds:
        state = alert.observe(t, value)
        t += step
    return t, state


class ThresholdAlertTests(unittest.TestCase):
    def test_trips_only_after_the_dwell_pushes_once_clears_once(self):
        alert, sent = threshold()
        t, state = run(alert, 0, 290, 90)
        self.assertEqual((sent, state), ([], False))
        t, state = run(alert, t, 600, 90)
        self.assertTrue(state)
        self.assertEqual(len(sent), 1)
        self.assertEqual(alert.entry()["kind"], "gpu_temp")
        t, state = run(alert, t, 30, 60)           # shorter than CLEAR_S
        self.assertTrue(state)
        t, state = run(alert, t, 120, 60)
        self.assertFalse(state)
        self.assertEqual([p for _, _, p in sent], ["default", "low"])
        self.assertIsNone(alert.entry())

    def test_short_excursion_is_ignored(self):
        alert, sent = threshold()
        t, _ = run(alert, 0, 200, 95)
        run(alert, t, 600, 60)
        self.assertEqual(sent, [])

    def test_unknown_reading_neither_trips_nor_clears(self):
        alert, sent = threshold()
        run(alert, 0, 3600, None)
        self.assertEqual(sent, [])
        t, state = run(alert, 0, 400, 90)
        self.assertTrue(state)
        t, state = run(alert, t, 3600, None)       # sensor lost while hot
        self.assertTrue(state)
        self.assertEqual(len(sent), 1)

    def test_disk_trips_immediately_and_zero_limit_is_off(self):
        n, ntfy, _ = notifier()
        disk = ThresholdAlert("disk", "Disk usage", " %", 90, 0, n)
        self.assertFalse(disk.observe(0, 89))
        self.assertTrue(disk.observe(2, 91))
        self.assertEqual(len(ntfy), 1)
        off = ThresholdAlert("disk", "Disk usage", " %", 0, 0, n)
        self.assertFalse(off.observe(0, 100))
        self.assertEqual(len(ntfy), 1)


class BudgetAlertTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "p.db")
        self.cost = 0.0
        self.n, self.ntfy, _ = notifier()

    def tearDown(self):
        self.tmp.cleanup()

    def alert(self, budget=50):
        return BudgetAlert(budget, "EUR", self.store, self.n, lambda when: self.cost)

    def test_fires_once_per_month_and_again_next_month(self):
        a = self.alert()
        jan = datetime(2026, 1, 10, 12).timestamp()
        a.observe(jan)
        self.cost = 60
        a.observe(jan + 100)                       # inside the 5-minute window: not checked
        self.assertEqual(len(self.ntfy), 0)
        a.observe(jan + 400)
        a.observe(jan + 4000)
        self.assertEqual(len(self.ntfy), 1)
        self.assertEqual(a.entry(jan + 4000)["kind"], "budget")
        feb = datetime(2026, 2, 2, 12).timestamp()
        self.assertIsNone(a.entry(feb))
        a.observe(feb)
        self.assertEqual(len(self.ntfy), 2)

    def test_restart_in_the_same_month_does_not_resend(self):
        jan = datetime(2026, 1, 10, 12).timestamp()
        self.cost = 60
        self.alert().observe(jan)
        again = self.alert()                       # a fresh process, same database
        again.observe(jan + 3600)
        self.assertEqual(len(self.ntfy), 1)
        self.assertIsNotNone(again.entry(jan + 3600))

    def test_under_budget_and_off(self):
        jan = datetime(2026, 1, 10, 12).timestamp()
        self.cost = 40
        self.alert().observe(jan)
        self.cost = 1000
        self.alert(budget=0).observe(jan + 4000)
        self.assertEqual(self.ntfy, [])


if __name__ == "__main__":
    unittest.main()
