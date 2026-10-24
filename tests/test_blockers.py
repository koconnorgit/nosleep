import unittest

from nosleep.engine import blocker_lines, blockers_headline
from nosleep.inhibitor import other_inhibitions


class OtherInhibitionsTests(unittest.TestCase):
    def test_filters_our_own_entry(self):
        rows = [["nosleep", "Scheduled awake window 09:00–00:00"], ["firefox", "Playing audio"]]
        self.assertEqual(other_inhibitions(rows), [("firefox", "Playing audio")])

    def test_dedupes_and_tolerates_short_rows(self):
        rows = [["firefox", "Playing audio"], ["firefox", "Playing audio"], ["vlc"], [], ["", "x"]]
        self.assertEqual(other_inhibitions(rows), [("firefox", "Playing audio"), ("vlc", "")])

    def test_empty(self):
        self.assertEqual(other_inhibitions([]), [])
        self.assertEqual(other_inhibitions([["nosleep", "r"]]), [])


class TextTests(unittest.TestCase):
    def test_headline_counts_apps_not_reasons(self):
        self.assertEqual(blockers_headline([("firefox", "a")]), "firefox is blocking it")
        self.assertEqual(blockers_headline([("firefox", "a"), ("firefox", "b")]), "firefox is blocking it")
        self.assertEqual(blockers_headline([("vlc", ""), ("firefox", "a")]), "firefox and vlc are blocking it")
        self.assertEqual(blockers_headline([("a", ""), ("b", ""), ("c", "")]), "3 apps are blocking it")

    def test_lines(self):
        self.assertEqual(blocker_lines([], awake=False), [])
        self.assertEqual(blocker_lines([("firefox", "Playing audio")], awake=False),
                         ["Blocking sleep: firefox (Playing audio)"])
        self.assertEqual(blocker_lines([("vlc", "")], awake=True), ["Also blocking sleep: vlc"])


if __name__ == "__main__":
    unittest.main()
