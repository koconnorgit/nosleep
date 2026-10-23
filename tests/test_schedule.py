import unittest
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from nosleep.schedule import (
    Override,
    desired_state,
    fmt_when,
    in_window,
    next_transition,
)

TZ = ZoneInfo("America/Detroit")


def dt(y, mo, d, h, mi=0, tz=TZ):
    return datetime(y, mo, d, h, mi, tzinfo=tz)


class InWindowTests(unittest.TestCase):
    def test_simple_window(self):
        s, e = time(9), time(17)
        self.assertFalse(in_window(time(8, 59), s, e))
        self.assertTrue(in_window(time(9, 0), s, e))
        self.assertTrue(in_window(time(12), s, e))
        self.assertTrue(in_window(time(16, 59), s, e))
        self.assertFalse(in_window(time(17, 0), s, e))
        self.assertFalse(in_window(time(23), s, e))

    def test_default_window_to_midnight(self):
        s, e = time(9), time(0)
        self.assertTrue(in_window(time(9), s, e))
        self.assertTrue(in_window(time(15), s, e))
        self.assertTrue(in_window(time(23, 59), s, e))
        self.assertFalse(in_window(time(0, 0), s, e))
        self.assertFalse(in_window(time(3), s, e))
        self.assertFalse(in_window(time(8, 59), s, e))

    def test_crosses_midnight(self):
        s, e = time(21), time(2)
        self.assertTrue(in_window(time(21), s, e))
        self.assertTrue(in_window(time(23, 30), s, e))
        self.assertTrue(in_window(time(0), s, e))
        self.assertTrue(in_window(time(1, 59), s, e))
        self.assertFalse(in_window(time(2), s, e))
        self.assertFalse(in_window(time(12), s, e))

    def test_equal_means_always(self):
        for t in (time(0), time(9), time(23, 59)):
            self.assertTrue(in_window(t, time(9), time(9)))


class NextTransitionTests(unittest.TestCase):
    def test_inside_simple_window(self):
        self.assertEqual(
            next_transition(dt(2026, 10, 23, 12), time(9), time(17)),
            dt(2026, 10, 23, 17),
        )

    def test_before_simple_window(self):
        self.assertEqual(
            next_transition(dt(2026, 10, 23, 7), time(9), time(17)),
            dt(2026, 10, 23, 9),
        )

    def test_after_simple_window_rolls_to_tomorrow(self):
        self.assertEqual(
            next_transition(dt(2026, 10, 23, 18), time(9), time(17)),
            dt(2026, 10, 24, 9),
        )

    def test_default_window_inside_goes_to_midnight(self):
        self.assertEqual(
            next_transition(dt(2026, 10, 23, 15), time(9), time(0)),
            dt(2026, 10, 24, 0),
        )

    def test_default_window_after_midnight_goes_to_start(self):
        self.assertEqual(
            next_transition(dt(2026, 10, 24, 0, 0), time(9), time(0)),
            dt(2026, 10, 24, 9),
        )

    def test_exactly_at_start(self):
        # At 09:00 sharp we're inside, so the next change is the end.
        self.assertEqual(
            next_transition(dt(2026, 10, 23, 9, 0), time(9), time(0)),
            dt(2026, 10, 24, 0),
        )

    def test_crossing_window_early_morning(self):
        self.assertEqual(
            next_transition(dt(2026, 10, 23, 1), time(21), time(2)),
            dt(2026, 10, 23, 2),
        )

    def test_crossing_window_evening(self):
        self.assertEqual(
            next_transition(dt(2026, 10, 23, 22), time(21), time(2)),
            dt(2026, 10, 24, 2),
        )

    def test_always_has_no_transition(self):
        self.assertIsNone(next_transition(dt(2026, 10, 23, 12), time(9), time(9)))

    def test_naive_datetimes_work(self):
        self.assertEqual(
            next_transition(datetime(2026, 10, 23, 12), time(9), time(17)),
            datetime(2026, 10, 23, 17),
        )

    def test_spring_forward_day(self):
        # 2026-03-08: clocks jump 02:00 -> 03:00 in Detroit.
        # Window 09:00 -> 00:00; at 01:30 we're asleep, next is 09:00 wall clock.
        n = next_transition(dt(2026, 3, 8, 1, 30), time(9), time(0))
        self.assertEqual((n.hour, n.minute, n.day), (9, 0, 8))
        # Awake at 15:00 on DST day: next is midnight tonight, wall clock.
        n = next_transition(dt(2026, 3, 8, 15), time(9), time(0))
        self.assertEqual((n.hour, n.minute, n.day), (0, 0, 9))

    def test_fall_back_day(self):
        # 2026-11-01: 02:00 EDT -> 01:00 EST. The 01:xx hour happens twice.
        s, e = time(21), time(2)
        first_one = datetime(2026, 11, 1, 1, 30, tzinfo=TZ, fold=0)
        second_one = datetime(2026, 11, 1, 1, 30, tzinfo=TZ, fold=1)
        for now in (first_one, second_one):
            self.assertTrue(in_window(now.time(), s, e))
            n = next_transition(now, s, e)
            self.assertEqual((n.hour, n.minute, n.day), (2, 0, 1))


class DesiredStateTests(unittest.TestCase):
    S, E = time(9), time(0)

    def test_schedule_awake(self):
        st = desired_state(dt(2026, 10, 23, 12), self.S, self.E, True)
        self.assertTrue(st.awake)
        self.assertEqual(st.source, "schedule")
        self.assertEqual(st.next_change, dt(2026, 10, 24, 0))

    def test_schedule_asleep(self):
        st = desired_state(dt(2026, 10, 23, 3), self.S, self.E, True)
        self.assertFalse(st.awake)
        self.assertEqual(st.next_change, dt(2026, 10, 23, 9))

    def test_disabled(self):
        st = desired_state(dt(2026, 10, 23, 12), self.S, self.E, False)
        self.assertFalse(st.awake)
        self.assertEqual(st.source, "disabled")
        self.assertIsNone(st.next_change)

    def test_always(self):
        st = desired_state(dt(2026, 10, 23, 12), time(9), time(9), True)
        self.assertTrue(st.awake)
        self.assertEqual(st.source, "always")

    def test_override_forces_sleep_inside_window(self):
        exp = dt(2026, 10, 24, 0)
        st = desired_state(dt(2026, 10, 23, 12), self.S, self.E, True, Override(False, exp))
        self.assertFalse(st.awake)
        self.assertEqual(st.source, "override")
        self.assertEqual(st.next_change, exp)

    def test_override_forces_awake_outside_window(self):
        exp = dt(2026, 10, 23, 9)
        st = desired_state(dt(2026, 10, 23, 3), self.S, self.E, True, Override(True, exp))
        self.assertTrue(st.awake)
        self.assertEqual(st.source, "override")

    def test_expired_override_is_ignored(self):
        exp = dt(2026, 10, 23, 9)
        st = desired_state(dt(2026, 10, 23, 9), self.S, self.E, True, Override(False, exp))
        self.assertTrue(st.awake)
        self.assertEqual(st.source, "schedule")

    def test_override_without_expiry_when_disabled(self):
        st = desired_state(dt(2026, 10, 23, 12), self.S, self.E, False, Override(True))
        self.assertTrue(st.awake)
        self.assertIsNone(st.next_change)


class FormatTests(unittest.TestCase):
    def test_fmt_when(self):
        now = dt(2026, 10, 23, 12)
        self.assertEqual(fmt_when(dt(2026, 10, 23, 17), now), "17:00")
        self.assertEqual(fmt_when(dt(2026, 10, 24, 0), now), "00:00 tomorrow")
        self.assertEqual(fmt_when(now + timedelta(days=3), now), "Mon 12:00")


if __name__ == "__main__":
    unittest.main()
