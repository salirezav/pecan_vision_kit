"""Kit number parsed from the usda-vision-## hostname."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from kit_id import apply_hostname_kit_id, kit_id_from_hostname  # noqa: E402


class KitIdFromHostnameTests(unittest.TestCase):
    def test_two_digit_hostname(self) -> None:
        self.assertEqual(kit_id_from_hostname("usda-vision-11"), "11")

    def test_leading_zero(self) -> None:
        self.assertEqual(kit_id_from_hostname("usda-vision-08"), "08")

    def test_single_digit_is_padded(self) -> None:
        self.assertEqual(kit_id_from_hostname("usda-vision-8"), "08")

    def test_fqdn(self) -> None:
        self.assertEqual(kit_id_from_hostname("usda-vision-11.local"), "11")

    def test_case_insensitive(self) -> None:
        self.assertEqual(kit_id_from_hostname("USDA-VISION-11"), "11")

    def test_unrelated_hostname(self) -> None:
        self.assertIsNone(kit_id_from_hostname("pecancam-03"))

    def test_extra_digits_do_not_match(self) -> None:
        self.assertIsNone(kit_id_from_hostname("usda-vision-111"))

    def test_apply_replaces_stale_default_and_sets_env(self) -> None:
        data = {"kit_id": "01", "port": 8080}
        changed = apply_hostname_kit_id(data, "usda-vision-11")
        self.assertEqual(changed, "11")
        self.assertEqual(data["kit_id"], "11")
        self.assertEqual(data["port"], 8080)
        self.assertEqual(os.environ["KIT_ID"], "11")

    def test_apply_skips_write_when_already_correct(self) -> None:
        data = {"kit_id": "11"}
        self.assertIsNone(apply_hostname_kit_id(data, "usda-vision-11"))
        self.assertEqual(data["kit_id"], "11")

    def test_apply_leaves_config_when_hostname_has_no_kit_number(self) -> None:
        data = {"kit_id": "01"}
        self.assertIsNone(apply_hostname_kit_id(data, "desktop-pc"))
        self.assertEqual(data["kit_id"], "01")


if __name__ == "__main__":
    unittest.main()
