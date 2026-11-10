"""The Mir:ror's reports, as read from a real one on 2026-11-10.

    cd mirror && python3 -m unittest test_mirror
"""

import unittest

from mirror import UPRIGHT, UPSIDE_DOWN, parse


def report(*b: int) -> bytes:
    return bytes(b) + bytes(64 - len(b))


TAG = (0xD0, 0x02, 0x1A, 0x35, 0x03, 0x8F, 0x3D, 0xDC)


class Parse(unittest.TestCase):
    def test_a_tag_put_on_it(self):
        self.assertEqual(parse(report(0x02, 0x01, 0, 0, 8, *TAG)), ("on", "d0021a35038f3ddc"))

    def test_a_tag_taken_off(self):
        self.assertEqual(parse(report(0x02, 0x02, 0, 0, 8, *TAG)), ("off", "d0021a35038f3ddc"))

    def test_the_flips(self):
        self.assertEqual(parse(report(0x01, 0x05)), ("flip", UPSIDE_DOWN))
        self.assertEqual(parse(report(0x01, 0x04)), ("flip", UPRIGHT))

    def test_the_idle_reports_and_anything_unknown_are_nothing(self):
        self.assertIsNone(parse(bytes(64)))
        self.assertIsNone(parse(report(0x03, 0x01)))
        self.assertIsNone(parse(report(0x02, 0x01, 0, 0, 0)))
        self.assertIsNone(parse(b"\x02\x01\x00\x00\x08\xd0"))    # a cut report


if __name__ == "__main__":
    unittest.main()
