"""Checkpoint inspection and class metadata. No torch import."""

from __future__ import annotations

import io
import os
import pickle
import zipfile
from pathlib import Path
from typing import Any

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


def library_error(family: str | None = None) -> str | None:
    """Report missing model packages without importing torch into the web process."""
    import importlib.util

    if importlib.util.find_spec("torch") is None:
        return "The model library is not ready yet (torch is not installed)."
    if family == "ultralytics":
        if importlib.util.find_spec("ultralytics") is None:
            return "This is a YOLO model, and the YOLO library is not ready yet (ultralytics is not installed)."
        return None
    if importlib.util.find_spec("rfdetr") is None:
        return "The model library is not ready yet (rfdetr is not installed)."
    return None


def _library_error(family: str | None = None) -> str | None:
    return library_error(family)


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


