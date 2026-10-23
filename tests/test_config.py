import tempfile
import unittest
from datetime import time
from pathlib import Path

from nosleep.config import Config, load, parse_time, save


class ConfigTests(unittest.TestCase):
    def test_parse_time(self):
        self.assertEqual(parse_time("09:00"), time(9))
        self.assertEqual(parse_time("00:00"), time(0))
        self.assertEqual(parse_time("24:00"), time(0))
        self.assertEqual(parse_time(" 23:59 "), time(23, 59))
        for bad in ("25:00", "9", "09:60", "abc"):
            with self.assertRaises(ValueError):
                parse_time(bad)

    def test_roundtrip(self):
        cfg = Config(start=time(21, 30), end=time(2), enabled=False, inhibit_screen=True, notify=True)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sub" / "config.toml"
            save(cfg, p)
            self.assertEqual(load(p), cfg)
            self.assertIn('end = "02:00"', p.read_text())

    def test_missing_file_gives_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(load(Path(d) / "nope.toml"), Config())

    def test_bad_values_fall_back(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            p.write_text('start = "99:99"\nenabled = false\n')
            cfg = load(p)
            self.assertEqual(cfg.start, time(9))
            self.assertFalse(cfg.enabled)

    def test_corrupt_file_gives_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            p.write_text("this is not = = toml")
            self.assertEqual(load(p), Config())


if __name__ == "__main__":
    unittest.main()
