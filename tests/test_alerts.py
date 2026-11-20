"""Idle-draw alert: one push per episode, an urgent reminder, one all-clear.

Run from the repository root:  python3 -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powermon import IdleAlert  # noqa: E402


def make(**overrides):
    alerts = {"idle_w": 130, "after_min": 10, "remind_min": 60,
              "ntfy_url": "https://ntfy.example/topic", **overrides}
    sent = []
    alert = IdleAlert({"alerts": alerts}, send=lambda url, title, body, priority, tags:
                      sent.append((title, priority)))
    return alert, sent


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


if __name__ == "__main__":
    unittest.main()


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
