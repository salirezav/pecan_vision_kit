"""Unit tests for Ultralytics Hailo YOLOv8-seg export inspect and upload."""

from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from hailo_runtime import _detect_postprocess_kind, inspect_hef  # noqa: E402
from segment import library_weights_name  # noqa: E402
from segment_inspect import (  # noqa: E402
    extract_hailo_export_zip,
    infer_frame_max_side,
    inspect_hailo_export_dir,
    is_hailo_export_dir,
)


def _write_export(folder: Path, *, head: str = "Segment", arch: str = "hailo8", imgsz=640) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "best.hef").write_bytes(b"HEF" + b"\0" * 32)
    (folder / "metadata.yaml").write_text(
        "\n".join(
            [
                "task: segment",
                f"head: {head}",
                f"hailo_arch: {arch}",
                f"imgsz: {imgsz}",
                "names:",
                "  0: pecan_uncracked",
                "  1: pecan_open",
                "  2: kernel_region",
                "",
            ]
        )
    )
    return folder


class HailoExportInspectTests(unittest.TestCase):
    def test_accepts_yolov8_seg_hailo8_640(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = _write_export(Path(tmp) / "best_hailo_model")
            info = inspect_hailo_export_dir(folder)
            self.assertEqual(info["family"], "hailo")
            self.assertEqual(info["task"], "segment")
            self.assertEqual(info["backend"], "ultralytics")
            self.assertEqual(info["resolution"], 640)
            self.assertEqual(info["hailo_arch"], "hailo8")
            self.assertEqual(info["num_classes"], 3)
            self.assertEqual(info["class_names"][0], "pecan_uncracked")
            self.assertTrue(is_hailo_export_dir(folder))

    def test_rejects_segment26(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = _write_export(Path(tmp) / "bad_hailo_model", head="Segment26")
            with self.assertRaisesRegex(ValueError, "YOLO26 instance segmentation"):
                inspect_hailo_export_dir(folder)

    def test_rejects_hailo8l(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = _write_export(Path(tmp) / "bad_hailo_model", arch="hailo8l")
            with self.assertRaisesRegex(ValueError, "hailo_arch=hailo8"):
                inspect_hailo_export_dir(folder)

    def test_rejects_wrong_imgsz(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = _write_export(Path(tmp) / "bad_hailo_model", imgsz=512)
            with self.assertRaisesRegex(ValueError, "imgsz=640"):
                inspect_hailo_export_dir(folder)

    def test_zip_extract_and_library_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = _write_export(root / "pack" / "best_hailo_model")
            zip_path = root / "best_hailo_model.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.write(source / "best.hef", arcname="best_hailo_model/best.hef")
                archive.write(source / "metadata.yaml", arcname="best_hailo_model/metadata.yaml")
            dest = root / "out_hailo_model"
            extract_hailo_export_zip(zip_path, dest)
            self.assertTrue((dest / "best.hef").is_file())
            self.assertTrue((dest / "metadata.yaml").is_file())
            info = inspect_hailo_export_dir(dest)
            self.assertEqual(info["backend"], "ultralytics")
            self.assertEqual(library_weights_name("best_hailo_model.zip"), "best_hailo_model")
            self.assertEqual(library_weights_name("custom.zip"), "custom_hailo_model")

    def test_zip_ignores_extra_and_junk_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = _write_export(root / "pack" / "best_hailo_model")
            zip_path = root / "with_extras.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.write(source / "best.hef", arcname="best_hailo_model/best.hef")
                archive.write(
                    source / "metadata.yaml", arcname="best_hailo_model/metadata.yaml"
                )
                archive.writestr("best_hailo_model/notes.txt", "ignored")
                archive.writestr("__MACOSX/._metadata.yaml", "junk")
                archive.writestr("best_hailo_model/.DS_Store", "junk")
            dest = root / "out_hailo_model"
            extract_hailo_export_zip(zip_path, dest)
            self.assertTrue((dest / "best.hef").is_file())
            self.assertTrue((dest / "metadata.yaml").is_file())
            self.assertFalse((dest / "notes.txt").exists())
            inspect_hailo_export_dir(dest)

    def test_infer_size_locked_for_ultralytics_hailo(self) -> None:
        meta = {"family": "hailo", "backend": "ultralytics", "resolution": 640}
        self.assertEqual(infer_frame_max_side(meta, "fast"), 640)
        self.assertEqual(infer_frame_max_side(meta, "balanced"), 640)
        self.assertEqual(infer_frame_max_side(meta, "accurate"), 640)


class HailoPostprocessKindTests(unittest.TestCase):
    def test_ultralytics_seg_layout(self) -> None:
        # reg/cls/coeff × 3 + proto → 10 tensors; four of them end in 32.
        shapes = [
            (20, 20, 64),
            (20, 20, 6),
            (20, 20, 32),
            (40, 40, 64),
            (40, 40, 6),
            (40, 40, 32),
            (80, 80, 64),
            (80, 80, 6),
            (80, 80, 32),
            (160, 160, 32),
        ]
        infos = [SimpleNamespace(name=f"out{i}", shape=shape) for i, shape in enumerate(shapes)]
        self.assertEqual(_detect_postprocess_kind(infos), "ultralytics_seg")

    def test_yolov5_seg_layout_still_detected(self) -> None:
        shapes = [
            (20, 20, 351),
            (40, 40, 351),
            (80, 80, 351),
            (160, 160, 32),
        ]
        infos = [SimpleNamespace(name=f"out{i}", shape=shape) for i, shape in enumerate(shapes)]
        self.assertEqual(_detect_postprocess_kind(infos), "yolov5_seg")

    def test_bare_ultralytics_hef_rejected(self) -> None:
        shapes = [
            (20, 20, 64),
            (20, 20, 6),
            (20, 20, 32),
            (40, 40, 64),
            (40, 40, 6),
            (40, 40, 32),
            (80, 80, 64),
            (80, 80, 6),
            (80, 80, 32),
            (160, 160, 32),
        ]
        outputs = [SimpleNamespace(name=f"out{i}", shape=shape) for i, shape in enumerate(shapes)]
        hef = SimpleNamespace(
            get_input_vstream_infos=lambda: [SimpleNamespace(shape=(640, 640, 3))],
            get_output_vstream_infos=lambda: outputs,
            get_network_group_names=lambda: ["net"],
        )

        class _FakeHailoPlatform:
            HEF = staticmethod(lambda _p: hef)

        with mock.patch.dict(sys.modules, {"hailo_platform": _FakeHailoPlatform()}):
            with self.assertRaisesRegex(ValueError, "export zip"):
                inspect_hef(Path("fake.hef"))


class OptionalOnDeviceHailoTests(unittest.TestCase):
    def test_load_export_if_present(self) -> None:
        library = Path("/mnt/recordings/models/library")
        folder = None
        if library.is_dir():
            for path in sorted(library.glob("*_hailo_model")):
                if is_hailo_export_dir(path):
                    folder = path
                    break
        if folder is None:
            self.skipTest("No best_hailo_model export on this kit")
        if not Path("/dev/hailo0").exists():
            self.skipTest("Hailo-8 device missing")

        import numpy as np
        from segment_runtime import load_model, predict_frame

        meta = inspect_hailo_export_dir(folder)
        model = load_model(folder, meta)
        try:
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            ok, jpeg = __import__("cv2").imencode(".jpg", frame)
            self.assertTrue(ok)
            result = predict_frame(
                model,
                jpeg.tobytes(),
                meta,
                threshold=0.25,
                speed="accurate",
                draw=False,
            )
            self.assertTrue(result.get("ok"))
            instances = result.get("instances") or []
            # Empty scene is fine; the important part is the path ran on Hailo.
            self.assertIsInstance(instances, list)
        finally:
            close = getattr(model, "close", None)
            if callable(close):
                close()


if __name__ == "__main__":
    unittest.main()
