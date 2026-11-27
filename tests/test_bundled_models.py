"""Shipped models can be listed and copied into the model library."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from segment import ModelService, library_weights_name  # noqa: E402
from segment_inspect import inspect_hailo_export_dir  # noqa: E402


def _write_export(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "best.hef").write_bytes(b"HEF" + b"\0" * 32)
    (folder / "metadata.yaml").write_text(
        "\n".join(
            [
                "task: segment",
                "head: Segment",
                "hailo_arch: hailo8",
                "imgsz: 640",
                "names:",
                "  0: crack",
                "",
            ]
        )
    )


def _remember_meta(service: ModelService, weights: Path, filename: str, class_names: list[str]) -> dict:
    del class_names
    if weights.is_dir():
        info = inspect_hailo_export_dir(weights)
    else:
        info = {"family": "yolo", "task": "segment", "class_names": ["crack"]}
    info["filename"] = filename
    info["weights_file"] = weights.name
    info["id"] = weights.name
    service.library_dir().mkdir(parents=True, exist_ok=True)
    (service.library_dir() / f"{weights.name}.json").write_text(json.dumps(info))
    return info


class BundledModelTests(unittest.TestCase):
    def test_library_names_keep_spaces_out(self) -> None:
        self.assertEqual(library_weights_name("halfyield-model.pt"), "halfyield-model.pt")
        self.assertEqual(library_weights_name("Crack Analyst v4.pt"), "Crack-Analyst-v4.pt")
        self.assertEqual(
            library_weights_name("Crack Analyst v4.zip"),
            "Crack-Analyst-v4_hailo_model",
        )

    def test_copy_skips_existing_and_dismiss_hides_until_bytes_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            store = Path(tmp) / "store"
            bundled = root / "models"
            bundled.mkdir(parents=True)
            weights = bundled / "Crack Analyst v4.pt"
            weights.write_bytes(b"weights" * 200)
            service = ModelService(root, store_dir=store)
            with mock.patch.object(ModelService, "_write_meta", autospec=True, side_effect=_remember_meta):
                summary = service.install_bundled()
            self.assertEqual(summary["bundled_installed"][0]["id"], "Crack-Analyst-v4.pt")
            dest = store / "library" / "Crack-Analyst-v4.pt"
            self.assertEqual(dest.read_bytes(), weights.read_bytes())
            self.assertEqual(service.bundled_pending(), [])

            weights.write_bytes(b"newer-weights" * 200)
            dest.write_bytes(b"local-copy")
            again = service.install_bundled(["Crack Analyst v4.pt"])
            self.assertEqual(again["bundled_skipped"], ["Crack Analyst v4.pt"])
            self.assertEqual(dest.read_bytes(), b"local-copy")

            other = bundled / "halfyield-model.pt"
            other.write_bytes(b"half" * 300)
            self.assertEqual(service.bundled_pending()[0]["filename"], "halfyield-model.pt")
            service.dismiss_bundled(["halfyield-model.pt"])
            self.assertEqual(service.bundled_pending(), [])
            other.write_bytes(b"half-v2" * 300)
            self.assertEqual(service.bundled_pending()[0]["filename"], "halfyield-model.pt")

    def test_zip_is_extracted_as_a_hailo_library_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            store = Path(tmp) / "store"
            bundled = root / "models"
            bundled.mkdir(parents=True)
            export = root / "pack" / "best_hailo_model"
            _write_export(export)
            zip_path = bundled / "Crack Analyst v4.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.write(export / "best.hef", arcname="best_hailo_model/best.hef")
                archive.write(export / "metadata.yaml", arcname="best_hailo_model/metadata.yaml")
            service = ModelService(root, store_dir=store)
            summary = service.install_bundled()
            dest = store / "library" / "Crack-Analyst-v4_hailo_model"
            self.assertTrue((dest / "best.hef").is_file())
            self.assertTrue((dest / "metadata.yaml").is_file())
            self.assertEqual(summary["bundled_installed"][0]["filename"], "Crack Analyst v4.zip")
            self.assertEqual(service.bundled_pending(), [])
            active = json.loads((store / "library" / "active.json").read_text())
            self.assertEqual(active["id"], "Crack-Analyst-v4_hailo_model")


if __name__ == "__main__":
    unittest.main()
