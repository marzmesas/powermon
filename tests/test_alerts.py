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
