"""Model load and predict helpers for the inference worker.

Torch, rfdetr, and ultralytics are imported only from functions in this module.
The web process must not import this module.
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from segment_draw import (
    _build_measurements,
    _classify_result,
    _colorize_depth,
    _draw_semantic,
    _draw_ultralytics_instances,
    _as_numpy,
)
from segment_inspect import (
    SPEED_SIZES,
    _COLORS,
    _TASKS,
    inspect_checkpoint,
    parse_class_names,
    yolo_infer_size,
)

# Keep large model caches on the NVMe, not the SD card.
os.environ.setdefault("HF_HOME", "/mnt/recordings/models/hf")
os.environ.setdefault("TORCH_HOME", "/mnt/recordings/models/torch")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

def _library_error(family: str | None = None) -> str | None:
    """Import check used only inside the inference worker process."""
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


def apply_labels(model: Any, labels: list[str]) -> None:
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


def _load_model(path: Path, meta: dict[str, Any]) -> Any:
    family = str(meta.get("family") or inspect_checkpoint(path).get("family") or "rfdetr")
    if family == "hailo":
        from hailo_runtime import load_hailo  # noqa: PLC0415

        return load_hailo(path, meta)
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
        apply_labels(model, [str(name) for name in names])
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
        apply_labels(model, [str(name) for name in names])
    return model


def _run_ultralytics(
    model: Any,
    bgr: np.ndarray,
    threshold: float,
    labels: list[str],
    infer_size: int,
    task: str,
    *,
    draw: bool = True,
) -> tuple[np.ndarray, list[dict[str, Any]], dict[str, int]]:
    kwargs: dict[str, Any] = {
        "imgsz": int(infer_size),
        "device": "cpu",
        "verbose": False,
        "stream": False,
        "retina_masks": False,
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
            return (bgr.copy() if draw else bgr), [], {}
        return _draw_semantic(bgr, _as_numpy(semantic.data), labels)
    if task == "depth":
        depth = getattr(result, "depth", None)
        if depth is None or getattr(depth, "data", None) is None:
            return (bgr.copy() if draw else bgr), [], {}
        return _colorize_depth(bgr, _as_numpy(depth.data)), [], {}
    return _draw_ultralytics_instances(result, bgr, labels, task, draw=draw)


def _run_model(
    model: Any,
    bgr: np.ndarray,
    threshold: float,
    labels: list[str],
    infer_size: int | None = None,
    *,
    draw: bool = True,
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
    annotated = bgr.copy() if draw else bgr
    items: list[dict[str, Any]] = []
    if detections is None or getattr(detections, "xyxy", None) is None or len(detections) == 0:
        return annotated, items, {}

    class_ids = np.asarray(detections.class_id)
    confidences = np.asarray(detections.confidence)
    boxes = np.asarray(detections.xyxy)
    masks = getattr(detections, "mask", None)
    keep = list(range(len(class_ids)))

    overlay = annotated.copy() if draw else None
    drew_mask = False
    union_masks: dict[str, np.ndarray] = {}
    if masks is not None:
        mask_list = np.asarray(masks)
        for index in keep:
            if index >= len(mask_list):
                continue
            class_id = int(class_ids[index])
            region = np.asarray(mask_list[index])
            if region.ndim == 3:
                region = region[0]
            if region.shape[:2] != bgr.shape[:2]:
                region = cv2.resize(
                    region.astype(np.uint8),
                    (bgr.shape[1], bgr.shape[0]),
                    interpolation=cv2.INTER_NEAREST,
                )
            region_bool = region.astype(bool)
            name = labels[class_id] if 0 <= class_id < len(labels) else f"Class {class_id + 1}"
            if name not in union_masks:
                union_masks[name] = np.zeros(bgr.shape[:2], dtype=bool)
            union_masks[name] |= region_bool
            if draw and overlay is not None:
                color = _COLORS[class_id % len(_COLORS)]
                overlay[region_bool] = color
                drew_mask = True
    if draw and drew_mask and overlay is not None:
        annotated = cv2.addWeighted(overlay, 0.45, annotated, 0.55, 0)

    for index in keep:
        class_id = int(class_ids[index])
        confidence = float(confidences[index]) if index < len(confidences) else 0.0
        if 0 <= class_id < len(labels):
            name = labels[class_id]
        else:
            name = f"Class {class_id + 1}"
        if draw:
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

def predict_frame(
    model: Any,
    jpeg: bytes,
    meta: dict[str, Any],
    threshold: float,
    speed: str = "fast",
    *,
    draw: bool = True,
) -> dict[str, Any]:
    frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise RuntimeError("Could not read the camera frame.")
    threshold = min(0.95, max(0.05, float(threshold)))
    speed_key = speed if speed in SPEED_SIZES else "fast"
    started = time.perf_counter()
    family = str(meta.get("family") or "rfdetr")
    task = str(meta.get("task") or "segment")
    cached = getattr(model, "_pecan_labels", None)
    labels = list(cached) if isinstance(cached, (list, tuple)) else None
    if labels is None:
        labels = parse_class_names(
            ",".join(str(name) for name in (meta.get("class_names") or [])),
            int(meta.get("num_classes") or 0),
            family,
        )
        apply_labels(model, labels)
        try:
            setattr(model, "_pecan_labels", list(labels))
        except Exception:  # noqa: BLE001
            pass
    if family == "hailo":
        from hailo_runtime import run_hailo  # noqa: PLC0415

        annotated, items, class_areas = run_hailo(
            model, frame, threshold, labels, task, draw=draw
        )
    elif family == "ultralytics":
        infer_size = yolo_infer_size(int(meta.get("resolution") or 640), speed_key)
        annotated, items, class_areas = _run_ultralytics(
            model, frame, threshold, labels, infer_size, task, draw=draw
        )
    else:
        annotated, items, class_areas = _run_model(
            model,
            frame,
            threshold,
            labels,
            infer_size=SPEED_SIZES[speed_key],
            draw=draw,
        )
    image = None
    if draw:
        ok, buf = cv2.imencode(".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if not ok:
            raise RuntimeError("Could not encode the result image.")
        image = "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii")
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
        "image": image,
    }


def load_model(path: Path, meta: dict[str, Any]) -> Any:
    return _load_model(path, meta)

