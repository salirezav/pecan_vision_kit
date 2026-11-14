"""Model upload, metadata, and inference facade for the web process.

Heavy torch work runs in the inference worker (infer_worker.py). This module
must not import segment_runtime or torch.
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from infer_worker import InferClient
from segment_draw import _metric_labels
from segment_inspect import (
    MAX_UPLOAD_BYTES,
    SPEED_SIZES,
    STORE_DIR,
    ModelBusy,
    _finish_names,
    describe_model,
    inspect_checkpoint,
    label_slots,
    library_error,
    parse_class_names,
    split_names,
)

MAX_METRICS = 8
_METRIC_MEASURES = frozenset({"area", "count"})

# Re-export for server.py and callers.
__all__ = [
    "MAX_UPLOAD_BYTES",
    "ModelBusy",
    "ModelService",
]


class ModelService:
    def __init__(
        self,
        root: Path,
        store_dir: Path = STORE_DIR,
        infer: InferClient | None = None,
        recording_check: Any | None = None,
    ) -> None:
        self.root = root
        self.store_dir = store_dir
        self._infer = infer or InferClient()
        self._recording_check = recording_check
        self._loaded = False
        self._loading = False
        self._error: str | None = None

    def start_worker(self) -> None:
        self._infer.start()

    def stop_worker(self) -> None:
        self._infer.stop()

    def weights_path(self) -> Path:
        meta = {}
        path = self.meta_path()
        if path.is_file():
            try:
                raw = json.loads(path.read_text())
                if isinstance(raw, dict):
                    meta = raw
            except (OSError, json.JSONDecodeError):
                meta = {}
        stored = str(meta.get("weights_file") or "").strip()
        if stored:
            candidate = self.store_dir / stored
            if candidate.is_file():
                return candidate
        hef = self.store_dir / "current.hef"
        if hef.is_file():
            return hef
        return self.store_dir / "current.pt"

    def meta_path(self) -> Path:
        return self.store_dir / "current.json"

    def metrics_path(self) -> Path:
        return self.store_dir / "metrics.json"

    def ensure_seeded(self) -> None:
        dest = self.weights_path()
        if dest.is_file():
            if not self.meta_path().is_file():
                self._write_meta(dest, dest.name, [])
            elif not self._read_meta().get("family"):
                self._upgrade_meta(dest)
            return
        bundled = self.root / "weights.pt"
        if not bundled.is_file():
            return
        self.store_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundled, dest)
        self._write_meta(dest, bundled.name, [])

    def _read_meta(self) -> dict[str, Any]:
        path = self.meta_path()
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_meta(self, weights: Path, filename: str, class_names: list[str]) -> dict[str, Any]:
        info = _finish_names(inspect_checkpoint(weights), class_names)
        info["filename"] = filename
        info["weights_file"] = weights.name
        self.meta_path().write_text(json.dumps(info, indent=2))
        return info

    def _upgrade_meta(self, weights: Path) -> None:
        meta = self._read_meta()
        existing = [str(name).strip() for name in (meta.get("class_names") or []) if str(name).strip()]
        info = _finish_names(inspect_checkpoint(weights), existing)
        info["filename"] = str(meta.get("filename") or weights.name)
        self.meta_path().write_text(json.dumps(info, indent=2))

    def status(self) -> dict[str, Any]:
        try:
            self.ensure_seeded()
        except Exception as exc:  # noqa: BLE001
            self._error = str(exc)
        path = self.weights_path()
        meta = self._read_meta()
        installed = path.is_file()
        family = str(meta.get("family") or "rfdetr")
        num_classes = meta.get("num_classes")
        view = describe_model(meta) if installed else {}
        lib_error = library_error(family if installed else "rfdetr")
        return {
            "installed": installed,
            "filename": meta.get("filename") if installed else None,
            "bytes": path.stat().st_size if installed else 0,
            "family": meta.get("family") if installed else None,
            "task": meta.get("task") if installed else None,
            "num_classes": num_classes if installed else None,
            "label_slots": label_slots(int(num_classes or 0), family) if installed and num_classes is not None else None,
            "variant": meta.get("variant") if installed else None,
            "architecture": meta.get("architecture") if installed else None,
            "resolution": meta.get("resolution") if installed else None,
            "class_names": meta.get("class_names") or [] if installed else [],
            "names_from_file": bool(meta.get("names_from_file")) if installed else False,
            "loaded": self._loaded,
            "loading": self._loading or self._infer.busy,
            "running": self._infer.busy,
            "error": self._error,
            "library_ready": lib_error is None,
            "library_error": lib_error,
            "speeds": list(SPEED_SIZES.keys()),
            **view,
        }

    def install_upload(self, payload: bytes, filename: str, class_names_raw: str) -> dict[str, Any]:
        if len(payload) < 1024:
            raise ValueError("That file is too small to be a model.")
        if len(payload) > MAX_UPLOAD_BYTES:
            raise ValueError("That file is too large (limit 800 MB).")
        suffix = Path(filename).suffix.lower()
        if suffix not in {".pt", ".pth", ".hef"}:
            raise ValueError("Upload a .pt, .pth, or .hef weights file.")
        self.store_dir.mkdir(parents=True, exist_ok=True)
        temp = self.store_dir / f"upload{suffix}"
        temp.write_bytes(payload)
        try:
            info = inspect_checkpoint(temp)
            dest_name = "current.hef" if suffix == ".hef" else "current.pt"
            dest = self.store_dir / dest_name
            for stale in (self.store_dir / "current.pt", self.store_dir / "current.hef"):
                if stale.exists() or stale.is_symlink():
                    if stale != dest:
                        stale.unlink()
            if dest.exists() or dest.is_symlink():
                dest.unlink()
            temp.replace(dest)
            meta = _finish_names(info, split_names(class_names_raw))
            meta["filename"] = Path(filename).name
            meta["weights_file"] = dest_name
            self.meta_path().write_text(json.dumps(meta, indent=2))
        except Exception:
            if temp.exists():
                temp.unlink()
            raise
        self._loaded = False
        self._error = None
        return self.status()

    def update_names(self, class_names_raw: str) -> dict[str, Any]:
        self.ensure_seeded()
        path = self.weights_path()
        if not path.is_file():
            raise ValueError("Upload a model first.")
        meta = self._read_meta()
        family = str(meta.get("family") or inspect_checkpoint(path).get("family") or "rfdetr")
        if "num_classes" in meta:
            num_classes = int(meta.get("num_classes") or 0)
        else:
            num_classes = int(inspect_checkpoint(path)["num_classes"])
        meta["family"] = family
        meta["class_names"] = parse_class_names(class_names_raw, num_classes, family)
        meta["names_from_file"] = False
        meta["filename"] = meta.get("filename") or path.name
        self.meta_path().write_text(json.dumps(meta, indent=2))
        return self.status()

    def list_metrics(self) -> list[dict[str, Any]]:
        path = self.metrics_path()
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(data, list):
            return []
        return [row for row in data if isinstance(row, dict)]

    def save_metrics(self, raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            raise ValueError("Send a list of metrics.")
        if len(raw) > MAX_METRICS:
            raise ValueError(f"Keep at most {MAX_METRICS} metrics.")
        self.ensure_seeded()
        allowed = {str(name) for name in (self._read_meta().get("class_names") or []) if str(name).strip()}
        if not allowed:
            raise ValueError("Upload a model with class names before saving metrics.")
        existing = {
            str(row.get("id") or ""): row
            for row in self.list_metrics()
            if isinstance(row, dict) and row.get("id")
        }
        cleaned: list[dict[str, Any]] = []
        for index, row in enumerate(raw):
            if not isinstance(row, dict):
                raise ValueError(f"Metric {index + 1} is not an object.")
            name = str(row.get("name") or "").strip()
            if not name:
                raise ValueError(f"Metric {index + 1} needs a name.")
            measure = str(row.get("measure") or "").strip().lower()
            if measure not in _METRIC_MEASURES:
                raise ValueError(f"Metric {name!r} measure must be area or count.")
            metric_id = str(row.get("id") or "").strip() or uuid.uuid4().hex
            previous = existing.get(metric_id)
            label_allowed = set(allowed)
            if previous:
                for side in ("numerator", "denominator"):
                    for label in previous.get(side) or []:
                        label_allowed.add(str(label))
            numerator = _metric_labels(row.get("numerator"), "numerator", name, label_allowed)
            denominator = _metric_labels(row.get("denominator"), "denominator", name, label_allowed)
            cleaned.append(
                {
                    "id": metric_id,
                    "name": name,
                    "measure": measure,
                    "numerator": numerator,
                    "denominator": denominator,
                }
            )
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.metrics_path().write_text(json.dumps(cleaned, indent=2))
        return cleaned

    def predict_jpeg(
        self,
        jpeg: bytes,
        threshold: float,
        speed: str = "fast",
        *,
        draw: bool = True,
    ) -> dict[str, Any]:
        if jpeg is None:
            raise RuntimeError("The camera does not have a frame yet.")
        self.ensure_seeded()
        path = self.weights_path()
        if not path.is_file():
            raise RuntimeError("Upload a model first.")
        meta = self._read_meta()
        recording = False
        if self._recording_check is not None:
            try:
                recording = bool(self._recording_check())
            except Exception:  # noqa: BLE001
                recording = False
        self._loading = True
        try:
            result = self._infer.predict(
                jpeg,
                weights_path=path,
                meta=meta,
                threshold=threshold,
                speed=speed,
                recording=recording,
                draw=draw,
            )
        finally:
            self._loading = False
        if not result.get("ok"):
            error = str(result.get("error") or "Inference failed.")
            self._error = error
            raise RuntimeError(error)
        self._loaded = True
        self._error = None
        return result

    def infer_meta(self) -> dict[str, Any]:
        self.ensure_seeded()
        return self._read_meta()
