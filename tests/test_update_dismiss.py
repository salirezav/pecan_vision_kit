"""Dismissing a finished update banner sticks until the next update."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import update  # noqa: E402


class DismissUpdateStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "update-status.json"
        self._orig = update.UPDATE_STATUS_PATH
        update.UPDATE_STATUS_PATH = self.path

    def tearDown(self) -> None:
        update.UPDATE_STATUS_PATH = self._orig
        self._tmp.cleanup()

    def _write(self, data: dict) -> None:
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def test_success_is_marked_without_changing_when_it_finished(self) -> None:
        self._write(
            {
                "state": "ok",
                "target_version": "0.2.5",
                "message": "Updated to 0.2.5 successfully",
                "updated_at": 1795534748.197029,
            }
        )
        self.assertTrue(update.dismiss_update_status())
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertTrue(saved["dismissed"])
        self.assertEqual(saved["updated_at"], 1795534748.197029)
        self.assertEqual(saved["target_version"], "0.2.5")
        self.assertTrue(update.dismiss_update_status())

    def test_failure_can_be_dismissed(self) -> None:
        self._write({"state": "failed", "target_version": "0.2.5", "updated_at": 10})
        self.assertTrue(update.dismiss_update_status())
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["state"], "failed")
        self.assertTrue(saved["dismissed"])

    def test_in_progress_update_is_left_alone(self) -> None:
        self._write({"state": "installing", "target_version": "0.2.6", "updated_at": 10})
        self.assertFalse(update.dismiss_update_status())
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertNotIn("dismissed", saved)

    def test_missing_status_is_a_no_op(self) -> None:
        self.assertFalse(update.dismiss_update_status())
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
