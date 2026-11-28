"""Renamed class names can be restored to the names stored in the model."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from segment import ModelService  # noqa: E402
from segment_inspect import _finish_names, file_default_names  # noqa: E402


def _service(root: Path, store: Path) -> ModelService:
    service = ModelService(root, store_dir=store)
    weights = service.library_dir() / "pecan.pt"
    weights.parent.mkdir(parents=True, exist_ok=True)
    weights.write_bytes(b"weights")
    return service


def _write_meta(service: ModelService, **extra: object) -> None:
    meta = {
        "family": "ultralytics",
        "task": "segment",
        "num_classes": 2,
        "filename": "pecan.pt",
        "weights_file": "pecan.pt",
        "id": "pecan.pt",
        "class_names": ["pecan_uncracked", "pecan_hairline"],
        "names_from_file": True,
    }
    meta.update(extra)
    service._sidecar("pecan.pt").write_text(json.dumps(meta))
    service._set_active("pecan.pt")


class ResetNamesTests(unittest.TestCase):
    def test_finish_names_keeps_file_names_when_overridden(self) -> None:
        info = _finish_names(
            {
                "family": "ultralytics",
                "task": "segment",
                "num_classes": 2,
                "class_names": ["pecan_uncracked", "pecan_hairline"],
            },
            ["good", "bad"],
        )
        self.assertEqual(info["class_names"], ["good", "bad"])
        self.assertFalse(info["names_from_file"])
        self.assertEqual(info["default_class_names"], ["pecan_uncracked", "pecan_hairline"])
        self.assertTrue(info["default_names_from_file"])

    def test_file_without_names_uses_generated_labels(self) -> None:
        names, from_file = file_default_names(
            {"family": "ultralytics", "num_classes": 2, "class_names": []}
        )
        self.assertEqual(names, ["Class 1", "Class 2"])
        self.assertFalse(from_file)

    def test_rename_remembers_file_names_and_reset_restores_them(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            store = Path(tmp) / "store"
            root.mkdir()
            service = _service(root, store)
            _write_meta(service)
            with mock.patch("segment.inspect_checkpoint") as inspected:
                renamed = service.update_names("good, bad")
                restored = service.reset_names()
            inspected.assert_not_called()
            self.assertEqual(renamed["class_names"], ["good", "bad"])
            self.assertEqual(renamed["default_class_names"], ["pecan_uncracked", "pecan_hairline"])
            self.assertFalse(renamed["names_from_file"])
            self.assertEqual(restored["class_names"], ["pecan_uncracked", "pecan_hairline"])
            self.assertTrue(restored["names_from_file"])

    def test_reset_reads_the_checkpoint_when_defaults_were_not_saved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            store = Path(tmp) / "store"
            root.mkdir()
            service = _service(root, store)
            _write_meta(
                service,
                class_names=["good", "bad"],
                names_from_file=False,
            )
            checkpoint = {
                "family": "ultralytics",
                "task": "segment",
                "num_classes": 2,
                "class_names": ["pecan_uncracked", "pecan_hairline"],
            }
            with mock.patch("segment.inspect_checkpoint", return_value=checkpoint) as inspected:
                restored = service.reset_names()
                again = service.reset_names()
            self.assertEqual(inspected.call_count, 1)
            self.assertEqual(restored["class_names"], ["pecan_uncracked", "pecan_hairline"])
            self.assertEqual(again["class_names"], ["pecan_uncracked", "pecan_hairline"])
            self.assertTrue(again["names_from_file"])


if __name__ == "__main__":
    unittest.main()
