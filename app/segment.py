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
from metric_formula import formula_labels, normalize_metric, validate_formula
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
_WEIGHT_SUFFIXES = frozenset({".pt", ".pth", ".hef"})


def library_weights_name(filename: str) -> str:
    """Safe file name for a weights upload stored in the model library."""
    raw = Path(str(filename or "")).name
    suffix = Path(raw).suffix.lower()
    if suffix not in _WEIGHT_SUFFIXES:
        raise ValueError("Upload a .pt, .pth, or .hef weights file.")
    cleaned: list[str] = []
    for char in Path(raw).stem:
        cleaned.append(char if char.isalnum() or char in "-_" else "-")
    safe = "".join(cleaned).strip("-")
    while "--" in safe:
        safe = safe.replace("--", "-")
    safe = (safe[:80] or "model")
    return f"{safe}{suffix}"

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

    def library_dir(self) -> Path:
        return self.store_dir / "library"

    def weights_path(self) -> Path:
        active = self._active_id()
        folder = self.library_dir()
        if active:
            candidate = folder / active
            if self._is_library_weights(candidate):
                return candidate
        files = self._weight_files()
        if files:
            return files[0]
        return folder / "current.pt"

    def meta_path(self) -> Path:
        return self._sidecar(self.weights_path().name)

    def metrics_path(self) -> Path:
        return self.store_dir / "metrics.json"

    def active_path(self) -> Path:
        return self.library_dir() / "active.json"

    def _sidecar(self, weights_name: str) -> Path:
        return self.library_dir() / f"{weights_name}.json"

    def _read_json(self, path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _active_id(self) -> str:
        return str(self._read_json(self.active_path()).get("id") or "").strip()

    def _set_active(self, weights_name: str) -> None:
        self.library_dir().mkdir(parents=True, exist_ok=True)
        self.active_path().write_text(json.dumps({"id": weights_name}, indent=2))

    def _is_library_weights(self, path: Path) -> bool:
        if not path.is_file() or path.name.startswith("."):
            return False
        if path.suffix.lower() not in _WEIGHT_SUFFIXES:
            return False
        try:
            resolved = path.resolve()
            root = self.library_dir().resolve()
        except OSError:
            return False
        return resolved.parent == root

    def _weight_files(self) -> list[Path]:
        folder = self.library_dir()
        if not folder.is_dir():
            return []
        found = [path for path in folder.iterdir() if self._is_library_weights(path)]
        found.sort(key=lambda item: item.name.casefold())
        return found

    def ensure_seeded(self) -> None:
        self._adopt_legacy()
        self._ensure_sidecars()
        files = self._weight_files()
        if not files:
            self._seed_bundled()
            files = self._weight_files()
        if not files:
            return
        active = self._active_id()
        if not any(path.name == active for path in files):
            self._set_active(files[0].name)

    def _adopt_legacy(self) -> None:
        """Move the old single current.pt / current.hef into the library once."""
        self.library_dir().mkdir(parents=True, exist_ok=True)
        legacy_meta_path = self.store_dir / "current.json"
        legacy_meta = self._read_json(legacy_meta_path)
        moved = False
        for legacy_name in ("current.pt", "current.pth", "current.hef"):
            legacy = self.store_dir / legacy_name
            if not legacy.is_file():
                continue
            original = str(legacy_meta.get("filename") or "").strip() or legacy.name
            try:
                dest_name = library_weights_name(original)
            except ValueError:
                dest_name = legacy.name
            if Path(dest_name).suffix.lower() != legacy.suffix.lower():
                dest_name = library_weights_name(f"{Path(dest_name).stem}{legacy.suffix.lower()}")
            dest = self.library_dir() / dest_name
            if not dest.exists():
                shutil.move(str(legacy), dest)
                moved = True
            elif dest.is_file() and dest.stat().st_size == legacy.stat().st_size:
                legacy.unlink()
                moved = True
            if dest.is_file() and not self._sidecar(dest.name).is_file() and legacy_meta:
                info = dict(legacy_meta)
                info["filename"] = Path(original).name
                info["weights_file"] = dest.name
                info["id"] = dest.name
                self._sidecar(dest.name).write_text(json.dumps(info, indent=2))
            if dest.is_file() and not self._active_id():
                self._set_active(dest.name)
        legacy_left = any((self.store_dir / name).is_file() for name in ("current.pt", "current.pth", "current.hef"))
        if moved and not legacy_left and legacy_meta_path.is_file():
            try:
                legacy_meta_path.unlink()
            except OSError:
                pass

    def _seed_bundled(self) -> None:
        bundled = self.root / "weights.pt"
        if not bundled.is_file():
            return
        self.library_dir().mkdir(parents=True, exist_ok=True)
        dest = self.library_dir() / "weights.pt"
        if not dest.exists():
            shutil.copy2(bundled, dest)
        if not self._sidecar(dest.name).is_file():
            try:
                self._write_meta(dest, bundled.name, [])
            except Exception as exc:  # noqa: BLE001
                self._store_inspect_failure(dest, bundled.name, exc)
        if dest.is_file():
            self._set_active(dest.name)

    def _ensure_sidecars(self) -> None:
        for path in self._weight_files():
            sidecar = self._sidecar(path.name)
            meta = self._read_json(sidecar)
            if not sidecar.is_file():
                try:
                    self._write_meta(path, path.name, [])
                except Exception as exc:  # noqa: BLE001
                    self._store_inspect_failure(path, path.name, exc)
                continue
            if meta.get("family") or meta.get("inspect_error"):
                continue
            try:
                self._upgrade_meta(path)
            except Exception as exc:  # noqa: BLE001
                self._store_inspect_failure(path, str(meta.get("filename") or path.name), exc)

    def _read_meta(self) -> dict[str, Any]:
        return self._read_json(self.meta_path())

    def _write_meta(self, weights: Path, filename: str, class_names: list[str]) -> dict[str, Any]:
        info = _finish_names(inspect_checkpoint(weights), class_names)
        info["filename"] = filename
        info["weights_file"] = weights.name
        info["id"] = weights.name
        self.library_dir().mkdir(parents=True, exist_ok=True)
        self._sidecar(weights.name).write_text(json.dumps(info, indent=2))
        return info

    def _store_inspect_failure(self, weights: Path, filename: str, exc: Exception) -> None:
        info = {
            "filename": filename,
            "weights_file": weights.name,
            "id": weights.name,
            "inspect_error": str(exc),
        }
        self.library_dir().mkdir(parents=True, exist_ok=True)
        self._sidecar(weights.name).write_text(json.dumps(info, indent=2))

    def _upgrade_meta(self, weights: Path) -> None:
        meta = self._read_json(self._sidecar(weights.name))
        existing = [str(name).strip() for name in (meta.get("class_names") or []) if str(name).strip()]
        info = _finish_names(inspect_checkpoint(weights), existing)
        info["filename"] = str(meta.get("filename") or weights.name)
        info["weights_file"] = weights.name
        info["id"] = weights.name
        self._sidecar(weights.name).write_text(json.dumps(info, indent=2))

    def _model_rows(self, active_id: str | None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in self._weight_files():
            meta = self._read_json(self._sidecar(path.name))
            described = bool(meta.get("family") or meta.get("task"))
            view = describe_model(meta) if described else {}
            names = [str(name) for name in (meta.get("class_names") or [])] if described else []
            rows.append(
                {
                    "id": path.name,
                    "filename": meta.get("filename") or path.name,
                    "bytes": path.stat().st_size,
                    "active": path.name == active_id,
                    "family": meta.get("family"),
                    "task": meta.get("task"),
                    "architecture": meta.get("architecture") or meta.get("variant"),
                    "resolution": meta.get("resolution"),
                    "num_classes": meta.get("num_classes"),
                    "class_names": names,
                    "kind": view.get("kind"),
                    "facts": view.get("facts") or [],
                    "inspect_error": meta.get("inspect_error"),
                }
            )
        return rows

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
        described = installed and bool(meta.get("family") or meta.get("task"))
        view = describe_model(meta) if described else {}
        if installed and meta.get("inspect_error") and not described:
            lib_error = None
        else:
            lib_error = library_error(family if described else "rfdetr")
        active_id = path.name if installed else None
        return {
            "installed": installed,
            "active_id": active_id,
            "library_path": str(self.library_dir()),
            "models": self._model_rows(active_id),
            "filename": meta.get("filename") if installed else None,
            "bytes": path.stat().st_size if installed else 0,
            "family": meta.get("family") if installed else None,
            "task": meta.get("task") if installed else None,
            "num_classes": num_classes if installed else None,
            "label_slots": label_slots(int(num_classes or 0), family) if installed and num_classes is not None else None,
            "variant": meta.get("variant") if installed else None,
            "architecture": meta.get("architecture") if installed else None,
            "resolution": meta.get("resolution") if installed else None,
            "class_names": (meta.get("class_names") or []) if installed else [],
            "names_from_file": bool(meta.get("names_from_file")) if installed else False,
            "inspect_error": meta.get("inspect_error") if installed else None,
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
        if self._infer.busy:
            raise ModelBusy("The model is already working. Wait for it to finish.")
        if len(payload) < 1024:
            raise ValueError("That file is too small to be a model.")
        if len(payload) > MAX_UPLOAD_BYTES:
            raise ValueError("That file is too large (limit 800 MB).")
        dest_name = library_weights_name(filename)
        self.library_dir().mkdir(parents=True, exist_ok=True)
        temp = self.library_dir() / f".upload-{uuid.uuid4().hex}{Path(dest_name).suffix}"
        temp.write_bytes(payload)
        try:
            info = inspect_checkpoint(temp)
            dest = self.library_dir() / dest_name
            if dest.exists() or dest.is_symlink():
                dest.unlink()
            temp.replace(dest)
            meta = _finish_names(info, split_names(class_names_raw))
            meta["filename"] = Path(filename).name
            meta["weights_file"] = dest_name
            meta["id"] = dest_name
            self._sidecar(dest_name).write_text(json.dumps(meta, indent=2))
            self._set_active(dest_name)
        except Exception:
            if temp.exists():
                temp.unlink()
            raise
        self._loaded = False
        self._error = None
        return self.status()

    def select_model(self, model_id: str) -> dict[str, Any]:
        if self._infer.busy:
            raise ModelBusy("The model is already working. Wait for it to finish.")
        name = Path(str(model_id or "")).name
        if not name or name != str(model_id or "").strip() or name.startswith("."):
            raise ValueError("Choose a model from the list.")
        if Path(name).suffix.lower() not in _WEIGHT_SUFFIXES:
            raise ValueError("Choose a model from the list.")
        self.ensure_seeded()
        path = self.library_dir() / name
        if not self._is_library_weights(path):
            raise ValueError("That model is not on this kit.")
        self._set_active(name)
        self._loaded = False
        self._error = None
        return self.status()

    def update_names(self, class_names_raw: str) -> dict[str, Any]:
        if self._infer.busy:
            raise ModelBusy("The model is already working. Wait for it to finish.")
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
        meta["weights_file"] = path.name
        meta["id"] = path.name
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
        out: list[dict[str, Any]] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            normalized = normalize_metric(row)
            if normalized.get("id") and normalized.get("name") and normalized.get("formula"):
                out.append(normalized)
        return out

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
            metric_id = str(row.get("id") or "").strip() or uuid.uuid4().hex
            previous = existing.get(metric_id)
            label_allowed = set(allowed)
            if previous:
                for label in formula_labels(str(previous.get("formula") or "")):
                    label_allowed.add(label)
                for side in ("numerator", "denominator"):
                    for label in previous.get(side) or []:
                        label_allowed.add(str(label))
            normalized = normalize_metric({**row, "id": metric_id, "name": name})
            formula = validate_formula(str(normalized.get("formula") or ""), label_allowed)
            cleaned.append(
                {
                    "id": metric_id,
                    "name": name,
                    "formula": formula,
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
        roi: dict[str, Any] | None = None,
        roi_space: dict[str, Any] | None = None,
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
                roi=roi,
                roi_space=roi_space,
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

    def halfyield_weights_candidates(self) -> list[Path]:
        return [
            self.store_dir / "halfyield-model.pt",
            self.root / "weights" / "halfyield-model.pt",
            self.root / "halfyield-model.pt",
        ]

    def halfyield_weights_path(self) -> Path | None:
        for path in self.halfyield_weights_candidates():
            if path.is_file():
                return path
        return None

    def halfyield_status(self) -> dict[str, Any]:
        path = self.halfyield_weights_path()
        if path is None:
            return {
                "installed": False,
                "filename": None,
                "path": None,
                "class_names": [],
                "error": (
                    "Place halfyield-model.pt under weights/ or "
                    f"{self.store_dir}/halfyield-model.pt."
                ),
            }
        try:
            info = inspect_checkpoint(path)
            finished = _finish_names(info, [])
        except Exception as exc:  # noqa: BLE001
            return {
                "installed": True,
                "filename": path.name,
                "path": str(path),
                "class_names": [],
                "error": str(exc),
            }
        return {
            "installed": True,
            "filename": path.name,
            "path": str(path),
            "bytes": path.stat().st_size,
            "family": finished.get("family"),
            "task": finished.get("task"),
            "architecture": finished.get("architecture") or finished.get("variant"),
            "num_classes": finished.get("num_classes"),
            "class_names": finished.get("class_names") or [],
            "names_from_file": bool(finished.get("names_from_file")),
            "error": None,
            "meta": finished,
        }

    def predict_roi_jpeg(
        self,
        jpeg: bytes,
        *,
        roi: dict[str, Any],
        thresholds: dict[str, float],
        weights_path: Path | None = None,
        meta: dict[str, Any] | None = None,
        min_area_px: float = 30.0,
        draw: bool = True,
    ) -> dict[str, Any]:
        if jpeg is None:
            raise RuntimeError("No video frame is available.")
        path = Path(weights_path) if weights_path is not None else self.halfyield_weights_path()
        if path is None or not path.is_file():
            raise RuntimeError("Upload or place the half-yield model first.")
        if meta is None:
            info = inspect_checkpoint(path)
            meta = _finish_names(info, [])
        recording = False
        if self._recording_check is not None:
            try:
                recording = bool(self._recording_check())
            except Exception:  # noqa: BLE001
                recording = False
        self._loading = True
        try:
            result = self._infer.predict_roi(
                jpeg,
                weights_path=path,
                meta=meta,
                roi=roi,
                thresholds=thresholds,
                recording=recording,
                min_area_px=min_area_px,
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
