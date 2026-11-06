import unittest

from tapster.settings import parse_settings


class ParseSettingsTest(unittest.TestCase):
    def test_valid_values(self):
        self.assertEqual(parse_settings("250", True, "5", "2"), (0.25, 5, 2))

    def test_limit_is_ignored_when_disabled(self):
        self.assertEqual(parse_settings("100", False, "abc", "0"), (0.1, None, 0))

    def test_invalid_values(self):
        for args in (
            ("abc", False, "1", "0"),
            ("1", False, "1", "0"),
            ("100", True, "0", "0"),
            ("100", False, "1", "-1"),
            ("100", False, "1", "61"),
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                parse_settings(*args)
