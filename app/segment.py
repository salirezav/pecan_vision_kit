"""Load an uploaded checkpoint and run it on camera frames.

RF-DETR segmentation checkpoints and Ultralytics YOLO checkpoints (detection,
instance segmentation, classification, pose, rotated boxes, semantic
segmentation, and depth) are accepted. Torch, rfdetr, and ultralytics are
imported only when a model is actually loaded, so a missing model library
cannot stop recording.
"""

from __future__ import annotations

import base64
import io
import json
import os
import pickle
import shutil
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

import cv2
import numpy as np

# Keep large model caches on the NVMe, not the SD card.
os.environ.setdefault("HF_HOME", "/mnt/recordings/models/hf")
os.environ.setdefault("TORCH_HOME", "/mnt/recordings/models/torch")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

STORE_DIR = Path("/mnt/recordings/models")
MAX_UPLOAD_BYTES = 800 * 1024 * 1024
MAX_METRICS = 8
_METRIC_MEASURES = frozenset({"area", "count"})

# (class name, default resolution, decoder layers, queries)
_SEG_SPECS: tuple[tuple[str, int, int, int], ...] = (
    ("RFDETRSegNano", 312, 4, 100),
    ("RFDETRSegSmall", 384, 4, 100),
    ("RFDETRSegPreview", 432, 4, 200),
    ("RFDETRSegMedium", 432, 5, 200),
    ("RFDETRSegLarge", 504, 5, 200),
    ("RFDETRSegXLarge", 624, 6, 300),
    ("RFDETRSeg2XLarge", 768, 6, 300),
)

_COLORS = (
    (80, 170, 70),
    (60, 140, 220),
    (40, 180, 200),
    (220, 150, 50),
    (200, 80, 160),
    (220, 80, 80),
    (160, 120, 220),
    (180, 180, 70),
)

# Ultralytics class name -> task the kit can run.
_CLASS_TASKS = {
    "SegmentationModel": "segment",
    "DetectionModel": "detect",
    "ClassificationModel": "classify",
    "PoseModel": "pose",
    "OBBModel": "obb",
    "SemanticSegmentationModel": "semantic",
    "DepthModel": "depth",
    "WorldModel": "detect",
    "YOLOEModel": "detect",
    "YOLOESegModel": "segment",
    "RTDETRDetectionModel": "detect",
}
_HEAD_TASKS = {
    "Segment": "segment",
    "Detect": "detect",
    "Classify": "classify",
    "Pose": "pose",
    "OBB": "obb",
    "SemanticSegment": "semantic",
    "Depth": "depth",
}
_TASKS = {
    "segment": {
        "does": "Outlines each object it recognizes.",
        "check": "Check this frame",
        "confidence": "Confidence",
        "uses_confidence": True,
        "names": True,
        "result": "Model result",
        "empty": "The model result will show up here.",
        "result_mode": "counts",
    },
    "detect": {
        "does": "Draws a box around each object it recognizes.",
        "check": "Check this frame",
        "confidence": "Confidence",
        "uses_confidence": True,
        "names": True,
        "result": "Model result",
        "empty": "The model result will show up here.",
        "result_mode": "counts",
    },
    "classify": {
        "does": "Assigns one class to the whole picture.",
        "check": "Classify this frame",
        "confidence": "Minimum score",
        "uses_confidence": True,
        "names": True,
        "result": "Class scores",
        "empty": "The class scores will show up here.",
        "result_mode": "scores",
    },
    "pose": {
        "does": "Finds each object and marks its keypoints.",
        "check": "Check this frame",
        "confidence": "Confidence",
        "uses_confidence": True,
        "names": True,
        "result": "Model result",
        "empty": "The model result will show up here.",
        "result_mode": "counts",
    },
    "obb": {
        "does": "Draws a rotated box around each object it recognizes.",
        "check": "Check this frame",
        "confidence": "Confidence",
        "uses_confidence": True,
        "names": True,
        "result": "Model result",
        "empty": "The model result will show up here.",
        "result_mode": "counts",
    },
    "semantic": {
        "does": "Labels every pixel with a class.",
        "check": "Segment this frame",
        "confidence": "Confidence",
        "uses_confidence": False,
        "names": True,
        "result": "Class areas",
        "empty": "The segmentation will show up here.",
        "result_mode": "areas",
    },
    "depth": {
        "does": "Estimates how far each pixel is from the camera.",
        "check": "Estimate depth",
        "confidence": "Confidence",
        "uses_confidence": False,
        "names": False,
        "result": "Depth",
        "empty": "The depth picture will show up here.",
        "result_mode": "depth",
    },
}
_KIND = {
    ("ultralytics", "segment"): "YOLO segmentation",
    ("ultralytics", "detect"): "YOLO detection",
    ("ultralytics", "classify"): "YOLO classification",
    ("ultralytics", "pose"): "YOLO pose",
    ("ultralytics", "obb"): "YOLO rotated boxes",
    ("ultralytics", "semantic"): "YOLO semantic segmentation",
    ("ultralytics", "depth"): "YOLO depth",
    ("rfdetr", "segment"): "RF-DETR segmentation",
}
_STUB_CLASSES: dict[str, type] = {}


class _Stub:
    def __new__(cls, *args: Any, **kwargs: Any) -> "_Stub":
        return object.__new__(cls)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        return None

    def __setstate__(self, state: Any) -> None:
        self.state = state


def _rebuild_tensor(
    storage: Any,
    storage_offset: Any,
    size: Any,
    stride: Any,
    requires_grad: Any,
    backward_hooks: Any,
    metadata: Any = None,
) -> dict[str, Any]:
    return {"size": tuple(int(x) for x in size)}


class _CheckpointUnpickler(pickle.Unpickler):
    """Read tensor shapes from a trusted checkpoint without executing it."""

    def persistent_load(self, pid: Any) -> tuple[str, Any]:
        return ("pid", pid)

    def find_class(self, module: str, name: str) -> Any:
        if module.startswith(("torch", "numpy")):
            if name == "_rebuild_tensor_v2":
                return _rebuild_tensor
            return _Stub
        if module in {"collections", "argparse", "builtins", "copyreg", "__builtin__"}:
            return super().find_class(module, name)
        # Other classes are recorded, not executed. YOLO checkpoints name their
        # task and classes on the model object; reading that does not need to
        # run the model's own code.
        return _stub_class(module, name)

    def load_build(self) -> None:
        state = self.stack.pop()
        inst = self.stack[-1]
        setstate = getattr(inst, "__setstate__", None)
        if setstate is not None:
            setstate(state)
            return
        slotstate = None
        if isinstance(state, tuple) and len(state) == 2:
            state, slotstate = state
        if state:
            for key, value in state.items():
                inst.__dict__[key] = value
        if slotstate:
            for key, value in slotstate.items():
                setattr(inst, key, value)


def _checkpoint_object(path: Path) -> dict[str, Any]:
    if not zipfile.is_zipfile(path):
        raise ValueError("Upload a .pt or .pth weights file.")
    with zipfile.ZipFile(path) as archive:
        pickles = [name for name in archive.namelist() if name.endswith("/data.pkl") or name == "data.pkl"]
        if not pickles:
            raise ValueError("This file does not look like a PyTorch weights file.")
        payload = archive.read(pickles[0])
    obj = _CheckpointUnpickler(io.BytesIO(payload)).load()
    if not isinstance(obj, dict) or "model" not in obj:
        raise ValueError("This weights file has no model weights in it.")
    return obj


def _stub_class(module: str, name: str) -> type:
    key = f"{module}.{name}"
    cached = _STUB_CLASSES.get(key)
    if cached is None:
        cached = type(name, (_Stub,), {"_origin": key})
        _STUB_CLASSES[key] = cached
    return cached


def _origin_of(obj: Any) -> str:
    return str(getattr(type(obj), "_origin", "") or "")


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    state = getattr(value, "state", None)
    if isinstance(state, dict):
        return state
    attrs = getattr(value, "__dict__", None)
    if isinstance(attrs, dict):
        return {key: item for key, item in attrs.items() if not str(key).startswith("_")}
    return {}


def _known_task(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    return text if text in _TASKS else None


def _task_from_origin(origin: str) -> str | None:
    return _CLASS_TASKS.get(origin.rsplit(".", 1)[-1])


def _task_from_yaml(yaml: dict[str, Any]) -> str | None:
    head = yaml.get("head")
    if not isinstance(head, (list, tuple)) or not head:
        return None
    last = head[-1]
    if isinstance(last, (list, tuple)) and len(last) >= 3:
        return _HEAD_TASKS.get(str(last[2]))
    return None


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _image_size(*values: Any) -> int | None:
    for value in values:
        if isinstance(value, (list, tuple)) and value:
            value = value[0]
        size = _positive_int(value)
        if size:
            return size
    return None


def _architecture_name(*sources: Any) -> str:
    for source in sources:
        if not isinstance(source, str) or not source.strip():
            continue
        base = Path(source).name
        for ext in (".yaml", ".yml", ".pt", ".pth"):
            if base.lower().endswith(ext):
                base = base[: -len(ext)]
                break
        if base and base.lower() not in {"none", "null"}:
            return base
    return ""


def _class_name_list(names: Any) -> list[str]:
    if isinstance(names, dict):
        pairs: list[tuple[int, str]] = []
        for key, value in names.items():
            try:
                index = int(key)
            except (TypeError, ValueError):
                continue
            text = str(value).strip()
            if text:
                pairs.append((index, text))
        pairs.sort()
        return [text for _, text in pairs]
    if isinstance(names, (list, tuple)):
        return [str(value).strip() for value in names if str(value).strip()]
    return []


def _tensor_size(model: dict[str, Any], key: str) -> tuple[int, ...] | None:
    value = model.get(key)
    if isinstance(value, dict) and "size" in value:
        return tuple(value["size"])
    return None


def inspect_checkpoint(path: Path) -> dict[str, Any]:
    obj = _checkpoint_object(path)
    if not isinstance(obj, dict):
        if _origin_of(obj):
            return _inspect_ultralytics({"model": obj}, obj)
        raise ValueError("This weights file has no model weights in it.")
    model = obj.get("model")
    origin = _origin_of(model)
    if "ultralytics" in origin or _task_from_origin(origin):
        return _inspect_ultralytics(obj, model)
    if not isinstance(model, dict):
        if origin:
            raise ValueError(
                f"This weights file is {origin}, which this kit cannot run. "
                "Upload an RF-DETR segmentation model or a YOLO model."
            )
        raise ValueError("This weights file has no model weights in it.")
    return _inspect_rfdetr(obj, model)


def _inspect_ultralytics(obj: dict[str, Any], model: Any) -> dict[str, Any]:
    origin = _origin_of(model)
    state = _as_dict(model)
    train_args = _as_dict(obj.get("train_args"))
    args = _as_dict(state.get("args"))
    yaml_cfg = _as_dict(state.get("yaml"))
    task = (
        _task_from_origin(origin)
        or _known_task(args.get("task"))
        or _known_task(train_args.get("task"))
        or _task_from_yaml(yaml_cfg)
    )
    if task is None:
        pretty = origin or "an unrecognized model"
        raise ValueError(f"This weights file is {pretty}, which this kit cannot run.")
    names = _class_name_list(state.get("names"))
    num_classes = _positive_int(state.get("nc")) or _positive_int(yaml_cfg.get("nc")) or len(names)
    if names:
        num_classes = len(names)
    if task != "depth" and num_classes < 1:
        raise ValueError("Could not read how many classes this model has.")
    architecture = (
        _architecture_name(yaml_cfg.get("yaml_file"), args.get("model"), train_args.get("model")) or "yolo"
    )
    return {
        "family": "ultralytics",
        "task": task,
        "variant": architecture,
        "architecture": architecture,
        "num_classes": num_classes,
        "resolution": _image_size(args.get("imgsz"), train_args.get("imgsz")) or 640,
        "class_names": names,
    }


def _inspect_rfdetr(obj: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    if not any(str(key).startswith("segmentation_head") for key in model):
        raise ValueError("This weights file is not a segmentation model.")

    class_weight = _tensor_size(model, "class_embed.weight")
    query_weight = _tensor_size(model, "query_feat.weight")
    position = _tensor_size(model, "backbone.0.encoder.encoder.embeddings.position_embeddings")
    if class_weight is None or query_weight is None or position is None:
        raise ValueError("This segmentation file is missing weights the kit knows how to load.")
    if len(class_weight) < 1 or class_weight[0] < 2:
        raise ValueError("Could not read how many classes this model has.")

    decoder_layers = {
        int(key.split("decoder.layers.")[1].split(".")[0])
        for key in model
        if "decoder.layers." in key
    }
    if not decoder_layers:
        raise ValueError("Could not read this model's architecture.")

    args = obj.get("args") if isinstance(obj.get("args"), dict) else {}
    group = int(args.get("group_detr") or 13)
    queries_total = int(query_weight[0])
    num_queries = queries_total // group if group > 0 and queries_total % group == 0 else queries_total

    tokens = int(position[1])
    grid = int(round((tokens - 1) ** 0.5))
    if grid * grid + 1 != tokens:
        raise ValueError("Could not read this model's image size.")
    resolution = grid * 12
    num_classes = int(class_weight[0]) - 1
    variant = _match_variant(resolution, len(decoder_layers), num_queries)
    saved_names = args.get("class_names")
    class_names = [str(name) for name in saved_names] if isinstance(saved_names, list) else []
    return {
        "family": "rfdetr",
        "task": "segment",
        "variant": variant,
        "architecture": variant,
        "num_classes": num_classes,
        "resolution": resolution,
        "dec_layers": len(decoder_layers),
        "num_queries": num_queries,
        "class_names": class_names,
    }


def _match_variant(resolution: int, dec_layers: int, num_queries: int) -> str:
    exact = [
        spec
        for spec in _SEG_SPECS
        if spec[1] == resolution and spec[2] == dec_layers and spec[3] == num_queries
    ]
    if len(exact) == 1:
        return exact[0][0]
    same_arch = [spec for spec in _SEG_SPECS if spec[2] == dec_layers and spec[3] == num_queries]
    if same_arch:
        return min(same_arch, key=lambda spec: abs(spec[1] - resolution))[0]
    raise ValueError(
        "This segmentation model size is not one the kit can run "
        f"({resolution}px, {dec_layers} decoder layers, {num_queries} queries)."
    )


def label_slots(num_classes: int, family: str = "rfdetr") -> int:
    """How many class names the check screen should keep for this checkpoint.

    RF-DETR keeps one extra logit after the named training classes. That last
    slot is often described as background, but for the pecan model it carries
    the final class (Shell). YOLO uses one name per class stored in the file.
    """
    count = max(0, int(num_classes))
    if family == "rfdetr":
        return max(1, count + 1)
    return count


def split_names(raw: str) -> list[str]:
    return [part.strip() for part in raw.replace("\n", ",").split(",") if part.strip()]


def parse_class_names(raw: str, num_classes: int, family: str = "rfdetr") -> list[str]:
    names = split_names(raw)
    slots = label_slots(num_classes, family)
    if len(names) > slots:
        names = names[:slots]
    while len(names) < slots:
        names.append(f"Class {len(names) + 1}")
    return names


def class_colors(count: int) -> list[str]:
    colors: list[str] = []
    for index in range(count):
        blue, green, red = _COLORS[index % len(_COLORS)]
        colors.append(f"#{red:02x}{green:02x}{blue:02x}")
    return colors


def describe_model(meta: dict[str, Any]) -> dict[str, Any]:
    family = str(meta.get("family") or "rfdetr")
    task = str(meta.get("task") or "segment")
    spec = _TASKS.get(task) or _TASKS["segment"]
    names = [str(name) for name in (meta.get("class_names") or [])]
    architecture = str(meta.get("architecture") or meta.get("variant") or "")
    kind = _KIND.get((family, task), "Model")
    names_from_file = bool(meta.get("names_from_file"))
    if not spec["names"]:
        names_note = "This model does not use class names."
    elif names_from_file:
        names_note = "Class names were read from the weights file."
    elif family == "rfdetr":
        names_note = "This file does not include class names. The names below are saved on the kit."
    else:
        names_note = "These names were typed in on the kit."
    facts = [
        {"label": "Type", "value": kind},
        {"label": "Network", "value": architecture or "Unknown"},
    ]
    resolution = meta.get("resolution")
    if resolution:
        facts.append({"label": "Image size", "value": f"{resolution} px"})
    if spec["names"]:
        facts.append({"label": "Classes", "value": str(len(names))})
    return {
        "kind": kind,
        "does": spec["does"],
        "names_note": names_note,
        "check_label": spec["check"],
        "confidence_label": spec["confidence"],
        "result_label": spec["result"],
        "empty_label": spec["empty"],
        "uses_confidence": spec["uses_confidence"],
        "names_editable": spec["names"],
        "result_mode": spec["result_mode"],
        "facts": facts,
        "class_colors": class_colors(len(names)),
    }


def yolo_infer_size(native: int, speed: str) -> int:
    snapped = max(32, int(round(int(native or 640) / 32.0) * 32))
    if speed == "accurate":
        return snapped
    target = 320 if speed == "fast" else min(480, snapped)
    target = min(snapped, target)
    return max(32, int(round(target / 32.0) * 32))


# Infer sizes must stay divisible by patch_size * num_windows (12 * 2 = 24).
SPEED_SIZES = {
    "fast": 288,
    "balanced": 312,
    "accurate": None,  # model native resolution
}


def _library_error(family: str | None = None) -> str | None:
    # Imported on use so a missing model library cannot stop the recorder.
    try:
        import torch  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return f"The model library is not ready yet ({exc})."
    if family == "ultralytics":
        try:
            import ultralytics  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            return f"This is a YOLO model, and the YOLO library is not ready yet ({exc})."
        return None
    try:
        import rfdetr  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return f"The model library is not ready yet ({exc})."
    return None


def _finish_names(info: dict[str, Any], provided: list[str]) -> dict[str, Any]:
    family = str(info.get("family") or "rfdetr")
    num_classes = int(info.get("num_classes") or 0)
    file_names = [str(name) for name in (info.get("class_names") or []) if str(name).strip()]
    chosen = [name for name in provided if name]
    if chosen:
        info["class_names"] = parse_class_names(",".join(chosen), num_classes, family)
        info["names_from_file"] = False
    elif file_names:
        info["class_names"] = parse_class_names(",".join(file_names), num_classes, family)
        info["names_from_file"] = True
    else:
        info["class_names"] = parse_class_names("", num_classes, family)
        info["names_from_file"] = False
    return info


class ModelBusy(RuntimeError):
    """Raised when a check is requested while another check is still running."""


class ModelService:
    def __init__(self, root: Path, store_dir: Path = STORE_DIR) -> None:
        self.root = root
        self.store_dir = store_dir
        self._lock = threading.RLock()
        self._model: Any = None
        self._loaded_mtime: float | None = None
        self._loading = False
        self._running = False
        self._error: str | None = None

    def weights_path(self) -> Path:
        return self.store_dir / "current.pt"

    def meta_path(self) -> Path:
        return self.store_dir / "current.json"

    def metrics_path(self) -> Path:
        return self.store_dir / "metrics.json"

    def ensure_seeded(self) -> None:
        with self._lock:
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
        self.meta_path().write_text(json.dumps(info, indent=2))
        return info

    def _upgrade_meta(self, weights: Path) -> None:
        """Add task and family to a checkpoint saved before those fields existed."""
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
        library_error = _library_error(family if installed else "rfdetr")
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
            "loaded": self._model is not None,
            "loading": self._loading,
            "running": self._running,
            "error": self._error,
            "library_ready": library_error is None,
            "library_error": library_error,
            "speeds": list(SPEED_SIZES.keys()),
            **view,
        }

    def install_upload(self, payload: bytes, filename: str, class_names_raw: str) -> dict[str, Any]:
        if len(payload) < 1024:
            raise ValueError("That file is too small to be a model.")
        if len(payload) > MAX_UPLOAD_BYTES:
            raise ValueError("That file is too large (limit 800 MB).")
        suffix = Path(filename).suffix.lower()
        if suffix not in {".pt", ".pth"}:
            raise ValueError("Upload a .pt or .pth weights file.")
        self.store_dir.mkdir(parents=True, exist_ok=True)
        temp = self.store_dir / "upload.pt"
        temp.write_bytes(payload)
        try:
            inspect_checkpoint(temp)
            dest = self.weights_path()
            if dest.exists() or dest.is_symlink():
                dest.unlink()
            temp.replace(dest)
            self._write_meta(dest, Path(filename).name, split_names(class_names_raw))
        except Exception:
            if temp.exists():
                temp.unlink()
            raise
        with self._lock:
            self._model = None
            self._loaded_mtime = None
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
    ) -> dict[str, Any]:
        if jpeg is None:
            raise RuntimeError("The camera does not have a frame yet.")
        frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise RuntimeError("Could not read the camera frame.")
        threshold = min(0.95, max(0.05, float(threshold)))
        speed_key = speed if speed in SPEED_SIZES else "fast"
        started = time.perf_counter()
        with self._lock:
            if self._running or self._loading:
                raise ModelBusy("The model is already working. Wait for it to finish.")
            self._running = True
        try:
            model = self._ensure_model()
            meta = self._read_meta()
            labels = self._labels()
            self._apply_labels(model, labels)
            family = str(meta.get("family") or "rfdetr")
            task = str(meta.get("task") or "segment")
            if family == "ultralytics":
                infer_size = yolo_infer_size(int(meta.get("resolution") or 640), speed_key)
                annotated, items, class_areas = _run_ultralytics(
                    model, frame, threshold, labels, infer_size, task
                )
            else:
                annotated, items, class_areas = _run_model(
                    model,
                    frame,
                    threshold,
                    labels,
                    infer_size=SPEED_SIZES[speed_key],
                )
        finally:
            with self._lock:
                self._running = False
        ok, buf = cv2.imencode(".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            raise RuntimeError("Could not encode the result image.")

        task = str(self._read_meta().get("task") or "segment")
        result_mode = (_TASKS.get(task) or _TASKS["segment"])["result_mode"]
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        best = items[0] if items else None
        match = best if best and float(best.get("confidence") or 0) >= threshold else None
        counts: dict[str, int] = {}
        if result_mode == "counts":
            for item in items:
                counts[item["class"]] = counts.get(item["class"], 0) + 1
        measurements = _build_measurements(labels, counts, class_areas, result_mode)
        return {
            "ok": True,
            "elapsed_ms": elapsed_ms,
            "threshold": threshold,
            "speed": speed_key,
            "task": task,
            "result_mode": result_mode,
            "counts": [{"class": name, "count": count} for name, count in counts.items()],
            "areas": items if result_mode == "areas" else [],
            "measurements": measurements,
            "items": items[:40],
            "best": best,
            "match": match if result_mode == "scores" else None,
            "total": len(items) if result_mode == "counts" else (1 if match else 0),
            "image": "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii"),
        }

    def _labels(self) -> list[str]:
        meta = self._read_meta()
        names = meta.get("class_names") or []
        family = str(meta.get("family") or "rfdetr")
        if "num_classes" not in meta:
            return [str(name) for name in names]
        return parse_class_names(
            ",".join(str(name) for name in names),
            int(meta.get("num_classes") or 0),
            family,
        )

    @staticmethod
    def _apply_labels(model: Any, labels: list[str]) -> None:
        inner = getattr(model, "model", None)
        if inner is None:
            return
        try:
            inner.class_names = list(labels)
        except Exception:  # noqa: BLE001
            pass
        if hasattr(inner, "names"):
            try:
                inner.names = {index: name for index, name in enumerate(labels)}
            except Exception:  # noqa: BLE001
                pass

    def _ensure_model(self) -> Any:
        self.ensure_seeded()
        path = self.weights_path()
        if not path.is_file():
            raise RuntimeError("Upload a model first.")
        mtime = path.stat().st_mtime
        with self._lock:
            if self._model is not None and self._loaded_mtime == mtime:
                return self._model
            self._loading = True
        try:
            model = _load_model(path, self._read_meta())
        except Exception as exc:  # noqa: BLE001
            self._error = str(exc)
            raise
        else:
            self._error = None
        finally:
            with self._lock:
                self._loading = False
        with self._lock:
            self._model = model
            self._loaded_mtime = mtime
        return model


def _load_model(path: Path, meta: dict[str, Any]) -> Any:
    family = str(meta.get("family") or inspect_checkpoint(path).get("family") or "rfdetr")
    if family == "ultralytics":
        return _load_ultralytics(path, meta)
    return _load_rfdetr(path, meta)


def _configure_ultralytics() -> None:
    os.environ.setdefault("YOLO_VERBOSE", "False")
    try:
        from ultralytics.utils import SETTINGS

        if SETTINGS.get("sync"):
            SETTINGS.update({"sync": False})
    except Exception:  # noqa: BLE001
        return


def _load_ultralytics(path: Path, meta: dict[str, Any]) -> Any:
    missing = _library_error("ultralytics")
    if missing:
        raise RuntimeError(missing)
    import torch
    from ultralytics import YOLO

    torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
    _configure_ultralytics()
    # The person at the machine chose this file. YOLO checkpoints are full pickles.
    model = YOLO(str(path))
    names = meta.get("class_names") or []
    if names:
        ModelService._apply_labels(model, [str(name) for name in names])
    return model


def _load_rfdetr(path: Path, meta: dict[str, Any]) -> Any:
    missing = _library_error("rfdetr")
    if missing:
        raise RuntimeError(missing)
    import rfdetr
    import torch

    torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
    variant = str(meta.get("variant") or inspect_checkpoint(path)["variant"])
    num_classes = int(meta.get("num_classes") or inspect_checkpoint(path)["num_classes"])
    resolution = int(meta.get("resolution") or inspect_checkpoint(path)["resolution"])
    model_cls = getattr(rfdetr, variant, None)
    if model_cls is None:
        raise RuntimeError(f"This kit cannot load model type {variant}.")
    kwargs: dict[str, Any] = {
        "pretrain_weights": str(path),
        "num_classes": num_classes,
        "resolution": resolution,
        "device": "cpu",
    }
    if "XLarge" in variant:
        kwargs["accept_platform_model_license"] = True
    # Uploaded training checkpoints are full PyTorch pickles. The kit only loads
    # a file the person at the machine chose to upload.
    model = model_cls(trust_checkpoint=True, **kwargs)
    names = meta.get("class_names") or []
    if names:
        ModelService._apply_labels(model, [str(name) for name in names])
    return model


def _as_numpy(value: Any) -> np.ndarray:
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    return np.asarray(value)


def _metric_labels(
    raw: Any,
    side: str,
    metric_name: str,
    allowed: set[str],
) -> list[str]:
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"Metric {metric_name!r} needs at least one {side} label.")
    labels: list[str] = []
    seen: set[str] = set()
    for entry in raw:
        label = str(entry or "").strip()
        if not label:
            continue
        if label not in allowed:
            raise ValueError(f"Metric {metric_name!r} uses unknown label {label!r}.")
        if label in seen:
            continue
        seen.add(label)
        labels.append(label)
    if not labels:
        raise ValueError(f"Metric {metric_name!r} needs at least one {side} label.")
    return labels


def _build_measurements(
    labels: list[str],
    counts: dict[str, int],
    class_areas: dict[str, int],
    result_mode: str,
) -> list[dict[str, Any]]:
    """One row per class for ratio metrics. Classify and depth leave this empty."""
    if result_mode in {"scores", "depth"}:
        return []
    rows: list[dict[str, Any]] = []
    for name in labels:
        rows.append(
            {
                "class": name,
                "count": int(counts.get(name, 0)),
                "area": int(class_areas.get(name, 0)),
            }
        )
    return rows


def _run_ultralytics(
    model: Any,
    bgr: np.ndarray,
    threshold: float,
    labels: list[str],
    infer_size: int,
    task: str,
) -> tuple[np.ndarray, list[dict[str, Any]], dict[str, int]]:
    kwargs: dict[str, Any] = {
        "imgsz": int(infer_size),
        "device": "cpu",
        "verbose": False,
        "stream": False,
    }
    if task not in {"depth", "semantic"}:
        kwargs["conf"] = float(threshold)
    results = model.predict(bgr, **kwargs)
    if isinstance(results, list):
        result = results[0] if results else None
    else:
        result = results
    if result is None:
        raise RuntimeError("The model did not return a result.")
    if task == "classify":
        annotated, items = _classify_result(result, bgr, labels)
        return annotated, items, {}
    if task == "semantic":
        semantic = getattr(result, "semantic_mask", None)
        if semantic is None or getattr(semantic, "data", None) is None:
            return bgr.copy(), [], {}
        return _draw_semantic(bgr, _as_numpy(semantic.data), labels)
    if task == "depth":
        depth = getattr(result, "depth", None)
        if depth is None or getattr(depth, "data", None) is None:
            return bgr.copy(), [], {}
        return _colorize_depth(bgr, _as_numpy(depth.data)), [], {}
    return _draw_ultralytics_instances(result, bgr, labels, task)


def _classify_result(
    result: Any,
    bgr: np.ndarray,
    labels: list[str],
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    probs = getattr(result, "probs", None)
    if probs is None or getattr(probs, "data", None) is None:
        return bgr.copy(), []
    scores = _as_numpy(probs.data).astype(float).reshape(-1)
    order = sorted(range(len(scores)), key=lambda index: float(scores[index]), reverse=True)
    items: list[dict[str, Any]] = []
    for index in order:
        name = labels[index] if 0 <= index < len(labels) else f"Class {index + 1}"
        items.append({"class": name, "confidence": round(float(scores[index]), 3)})
    if not items:
        return bgr.copy(), []
    best = items[0]
    class_id = order[0]
    color = _COLORS[class_id % len(_COLORS)]
    annotated = bgr.copy()
    cv2.rectangle(annotated, (0, 0), (annotated.shape[1], 42), color, -1)
    cv2.putText(
        annotated,
        f"{best['class']} {best['confidence']:.0%}",
        (12, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    return annotated, items


def _draw_semantic(
    frame: np.ndarray,
    mask: np.ndarray,
    labels: list[str],
) -> tuple[np.ndarray, list[dict[str, Any]], dict[str, int]]:
    if mask.ndim == 3:
        mask = mask[0]
    mask = mask.astype(np.int32)
    if mask.shape[:2] != frame.shape[:2]:
        mask = cv2.resize(mask, (frame.shape[1], frame.shape[0]), interpolation=cv2.INTER_NEAREST)
    overlay = frame.copy()
    areas: list[dict[str, Any]] = []
    class_areas: dict[str, int] = {}
    total = int(mask.size) or 1
    class_ids = sorted(int(value) for value in np.unique(mask) if int(value) >= 0 and int(value) != 255)
    for class_id in class_ids:
        region = mask == class_id
        pixels = int(region.sum())
        if pixels <= 0:
            continue
        overlay[region] = _COLORS[class_id % len(_COLORS)]
        name = labels[class_id] if class_id < len(labels) else f"Class {class_id + 1}"
        areas.append({"class": name, "share": round(pixels / total, 4)})
        class_areas[name] = pixels
    areas.sort(key=lambda item: item["share"], reverse=True)
    return cv2.addWeighted(overlay, 0.45, frame, 0.55, 0), areas, class_areas


def _colorize_depth(frame: np.ndarray, depth: np.ndarray) -> np.ndarray:
    depth = np.asarray(depth, dtype=np.float32)
    if depth.ndim == 3:
        depth = depth[0]
    if depth.shape[:2] != frame.shape[:2]:
        depth = cv2.resize(depth, (frame.shape[1], frame.shape[0]), interpolation=cv2.INTER_LINEAR)
    valid = np.isfinite(depth)
    if not bool(valid.any()):
        return frame.copy()
    low, high = np.percentile(depth[valid], [2, 98])
    scale = max(float(high - low), 1e-6)
    norm = np.zeros(depth.shape, dtype=np.uint8)
    norm[valid] = np.clip((depth[valid] - low) / scale * 255.0, 0, 255).astype(np.uint8)
    colored = cv2.applyColorMap(norm, getattr(cv2, "COLORMAP_INFERNO", cv2.COLORMAP_JET))
    colored[~valid] = frame[~valid]
    return cv2.addWeighted(colored, 0.7, frame, 0.3, 0)


def _draw_ultralytics_instances(
    result: Any,
    frame: np.ndarray,
    labels: list[str],
    task: str,
) -> tuple[np.ndarray, list[dict[str, Any]], dict[str, int]]:
    boxes = None
    corners = None
    if task == "obb" and getattr(result, "obb", None) is not None and len(result.obb):
        obb = result.obb
        class_ids = _as_numpy(obb.cls).astype(int).reshape(-1)
        confidences = _as_numpy(obb.conf).astype(float).reshape(-1)
        corners = _as_numpy(obb.xyxyxyxy)
    else:
        detected = getattr(result, "boxes", None)
        if detected is None or len(detected) == 0:
            return frame.copy(), [], {}
        class_ids = _as_numpy(detected.cls).astype(int).reshape(-1)
        confidences = _as_numpy(detected.conf).astype(float).reshape(-1)
        boxes = _as_numpy(detected.xyxy).astype(float)
    polygons = None
    masks = getattr(result, "masks", None)
    if masks is not None and getattr(masks, "xy", None) is not None:
        polygons = masks.xy
    keypoints = None
    points = getattr(result, "keypoints", None)
    if points is not None and getattr(points, "xy", None) is not None:
        keypoints = _as_numpy(points.xy)

    annotated = frame.copy()
    overlay = annotated.copy()
    drew_mask = False
    items: list[dict[str, Any]] = []
    pending: list[tuple[int, float, str, tuple[int, int, int], tuple[int, int]]] = []
    polygons_by_class: dict[str, list[np.ndarray]] = {}
    for index, class_id in enumerate(class_ids):
        class_id = int(class_id)
        confidence = float(confidences[index]) if index < len(confidences) else 0.0
        name = labels[class_id] if 0 <= class_id < len(labels) else f"Class {class_id + 1}"
        color = _COLORS[class_id % len(_COLORS)]
        if polygons is not None and index < len(polygons):
            pts = np.asarray(polygons[index], dtype=np.int32)
            if pts.ndim == 2 and len(pts) >= 3:
                cv2.fillPoly(overlay, [pts], color)
                drew_mask = True
                polygons_by_class.setdefault(name, []).append(pts)
        anchor = _label_anchor(boxes, corners, index)
        pending.append((class_id, confidence, name, color, anchor))
        items.append({"class": name, "confidence": round(confidence, 3)})
    if drew_mask:
        annotated = cv2.addWeighted(overlay, 0.45, annotated, 0.55, 0)
    for index, (_class_id, confidence, name, color, anchor) in enumerate(pending):
        if corners is not None and index < len(corners):
            pts = np.asarray(corners[index], dtype=np.int32).reshape(-1, 2)
            cv2.polylines(annotated, [pts], True, color, 2)
        elif boxes is not None and index < len(boxes):
            x1, y1, x2, y2 = (int(round(float(v))) for v in boxes[index][:4])
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
        if keypoints is not None and index < len(keypoints):
            for point in np.asarray(keypoints[index]).reshape(-1, 2):
                x_pos, y_pos = int(round(float(point[0]))), int(round(float(point[1])))
                if x_pos > 0 or y_pos > 0:
                    cv2.circle(annotated, (x_pos, y_pos), 3, color, -1)
        x_pos, y_pos = anchor
        cv2.putText(
            annotated,
            f"{name} {confidence:.0%}",
            (x_pos, max(20, y_pos - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            color,
            2,
            cv2.LINE_AA,
        )
    items.sort(key=lambda item: item["confidence"], reverse=True)
    class_areas = _union_polygon_areas(frame.shape[:2], polygons_by_class)
    return annotated, items, class_areas


def _union_polygon_areas(
    shape: tuple[int, int],
    polygons_by_class: dict[str, list[np.ndarray]],
) -> dict[str, int]:
    if not polygons_by_class:
        return {}
    height, width = shape
    scratch = np.zeros((height, width), dtype=np.uint8)
    areas: dict[str, int] = {}
    for name, polys in polygons_by_class.items():
        scratch.fill(0)
        for pts in polys:
            cv2.fillPoly(scratch, [pts], 1)
        areas[name] = int(scratch.sum())
    return areas


def _label_anchor(boxes: np.ndarray | None, corners: np.ndarray | None, index: int) -> tuple[int, int]:
    if boxes is not None and index < len(boxes):
        return int(round(float(boxes[index][0]))), int(round(float(boxes[index][1])))
    if corners is not None and index < len(corners):
        pts = np.asarray(corners[index]).reshape(-1, 2)
        return int(np.min(pts[:, 0])), int(np.min(pts[:, 1]))
    return 8, 24


def _run_model(
    model: Any,
    bgr: np.ndarray,
    threshold: float,
    labels: list[str],
    infer_size: int | None = None,
) -> tuple[np.ndarray, list[dict[str, Any]], dict[str, int]]:
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    predict_kwargs: dict[str, Any] = {
        "threshold": threshold,
        "include_source_image": False,
    }
    if infer_size is not None:
        predict_kwargs["shape"] = (int(infer_size), int(infer_size))
    detections = model.predict(rgb, **predict_kwargs)
    if isinstance(detections, list):
        detections = detections[0] if detections else None
    annotated = bgr.copy()
    items: list[dict[str, Any]] = []
    if detections is None or getattr(detections, "xyxy", None) is None or len(detections) == 0:
        return annotated, items, {}

    class_ids = np.asarray(detections.class_id)
    confidences = np.asarray(detections.confidence)
    boxes = np.asarray(detections.xyxy)
    masks = getattr(detections, "mask", None)
    keep = list(range(len(class_ids)))

    overlay = annotated.copy()
    drew_mask = False
    union_masks: dict[str, np.ndarray] = {}
    if masks is not None:
        mask_list = np.asarray(masks)
        for index in keep:
            if index >= len(mask_list):
                continue
            class_id = int(class_ids[index])
            color = _COLORS[class_id % len(_COLORS)]
            region = np.asarray(mask_list[index])
            if region.ndim == 3:
                region = region[0]
            if region.shape[:2] != annotated.shape[:2]:
                region = cv2.resize(
                    region.astype(np.uint8),
                    (annotated.shape[1], annotated.shape[0]),
                    interpolation=cv2.INTER_NEAREST,
                )
            region_bool = region.astype(bool)
            overlay[region_bool] = color
            drew_mask = True
            name = labels[class_id] if 0 <= class_id < len(labels) else f"Class {class_id + 1}"
            if name not in union_masks:
                union_masks[name] = np.zeros(annotated.shape[:2], dtype=bool)
            union_masks[name] |= region_bool
    if drew_mask:
        annotated = cv2.addWeighted(overlay, 0.45, annotated, 0.55, 0)

    for index in keep:
        class_id = int(class_ids[index])
        confidence = float(confidences[index]) if index < len(confidences) else 0.0
        if 0 <= class_id < len(labels):
            name = labels[class_id]
        else:
            name = f"Class {class_id + 1}"
        color = _COLORS[class_id % len(_COLORS)]
        x1, y1, x2, y2 = (int(round(v)) for v in boxes[index][:4])
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
        caption = f"{name} {confidence:.0%}"
        cv2.putText(
            annotated,
            caption,
            (x1, max(20, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            color,
            2,
            cv2.LINE_AA,
        )
        items.append({"class": name, "confidence": round(confidence, 3)})
    items.sort(key=lambda item: item["confidence"], reverse=True)
    class_areas = {name: int(mask.sum()) for name, mask in union_masks.items()}
    return annotated, items, class_areas
