"""Model load and predict helpers for the inference worker.

Torch, rfdetr, and ultralytics are imported only from functions in this module.
The web process must not import this module.
"""

from __future__ import annotations

import base64
import math
import os
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from pecan_kpis import collect_frame_samples, empty_frame_samples, pecan_classes_ready
from segment_draw import (
    _build_measurements,
    _classify_result,
    _colorize_depth,
    _draw_semantic,
    _draw_ultralytics_instances,
    _as_numpy,
    draw_detection_label,
    draw_inspection_polygons,
    draw_roi_rectangle,
    overlay_thickness,
)
from segment_inspect import (
    SPEED_SIZES,
    _COLORS,
    _TASKS,
    infer_frame_max_side,
    inspect_checkpoint,
    parse_class_names,
    yolo_infer_size,
)
from yield_measure import (
    MODEL_SIZE,
    MIN_AREA_PX,
    class_ids_for,
    clip_roi_to_frame,
    scale_roi_to_frame,
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


def _class_cutoff(
    name: str,
    default: float,
    class_thresholds: dict[str, float] | None,
) -> float:
    if not class_thresholds:
        return float(default)
    if name in class_thresholds:
        return float(class_thresholds[name])
    return float(default)


def _run_ultralytics(
    model: Any,
    bgr: np.ndarray,
    threshold: float,
    labels: list[str],
    infer_size: int,
    task: str,
    *,
    draw: bool = True,
    reference_side: int | None = None,
    class_thresholds: dict[str, float] | None = None,
) -> tuple[np.ndarray, list[dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
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
        annotated, items = _classify_result(result, bgr, labels, reference_side)
        return annotated, items, {}, []
    if task == "semantic":
        semantic = getattr(result, "semantic_mask", None)
        if semantic is None or getattr(semantic, "data", None) is None:
            return (bgr.copy() if draw else bgr), [], {}, []
        annotated, items, class_areas = _draw_semantic(bgr, _as_numpy(semantic.data), labels)
        return annotated, items, class_areas, []
    if task == "depth":
        depth = getattr(result, "depth", None)
        if depth is None or getattr(depth, "data", None) is None:
            return (bgr.copy() if draw else bgr), [], {}, []
        return _colorize_depth(bgr, _as_numpy(depth.data)), [], {}, []
    return _draw_ultralytics_instances(
        result,
        bgr,
        labels,
        task,
        draw=draw,
        reference_side=reference_side,
        class_thresholds=class_thresholds,
        default_threshold=threshold,
    )


def _run_model(
    model: Any,
    bgr: np.ndarray,
    threshold: float,
    labels: list[str],
    infer_size: int | None = None,
    *,
    draw: bool = True,
    reference_side: int | None = None,
    class_thresholds: dict[str, float] | None = None,
) -> tuple[np.ndarray, list[dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
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
    instances: list[dict[str, Any]] = []
    if detections is None or getattr(detections, "xyxy", None) is None or len(detections) == 0:
        return annotated, items, {}, instances

    class_ids = np.asarray(detections.class_id)
    confidences = np.asarray(detections.confidence)
    boxes = np.asarray(detections.xyxy)
    masks = getattr(detections, "mask", None)
    keep: list[int] = []
    for index in range(len(class_ids)):
        class_id = int(class_ids[index])
        confidence = float(confidences[index]) if index < len(confidences) else 0.0
        name = labels[class_id] if 0 <= class_id < len(labels) else f"Class {class_id + 1}"
        if confidence < _class_cutoff(name, threshold, class_thresholds):
            continue
        keep.append(index)

    overlay = annotated.copy() if draw else None
    drew_mask = False
    union_masks: dict[str, np.ndarray] = {}
    mask_by_index: dict[int, np.ndarray] = {}
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
            mask_by_index[index] = region_bool
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
        x1, y1, x2, y2 = (float(v) for v in boxes[index][:4])
        instance: dict[str, Any] = {
            "class": name,
            "box": (x1, y1, x2, y2),
            "confidence": confidence,
        }
        if index in mask_by_index:
            instance["mask"] = mask_by_index[index]
            instance["area"] = float(mask_by_index[index].sum())
        instances.append(instance)
        if draw:
            color = _COLORS[class_id % len(_COLORS)]
            cv2.rectangle(
                annotated,
                (int(round(x1)), int(round(y1))),
                (int(round(x2)), int(round(y2))),
                color,
                overlay_thickness(annotated, reference_side),
            )
            draw_detection_label(
                annotated,
                f"{name} {confidence:.0%}",
                (int(round(x1)), int(round(y1))),
                color,
                reference_side,
            )
        items.append({"class": name, "confidence": round(confidence, 3)})
    items.sort(key=lambda item: item["confidence"], reverse=True)
    class_areas = {name: int(mask.sum()) for name, mask in union_masks.items()}
    return annotated, items, class_areas, instances

def _resolve_class_thresholds(
    labels: list[str],
    threshold: float,
    class_thresholds: dict[str, float] | None,
) -> tuple[float, dict[str, float]]:
    """Network cutoff (min) plus per-class map keyed by label name."""
    base = min(0.95, max(0.05, float(threshold)))
    if not class_thresholds:
        return base, {name: base for name in labels}
    cleaned: dict[str, float] = {}
    for name, value in class_thresholds.items():
        key = str(name)
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = base
        if not math.isfinite(number):
            number = base
        cleaned[key] = min(0.95, max(0.05, number))
    for name in labels:
        cleaned.setdefault(name, base)
    network = min(cleaned.values()) if cleaned else base
    return network, cleaned


def predict_frame(
    model: Any,
    jpeg: bytes,
    meta: dict[str, Any],
    threshold: float,
    speed: str = "fast",
    *,
    draw: bool = True,
    roi: dict[str, Any] | None = None,
    roi_space: dict[str, Any] | None = None,
    class_thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise RuntimeError("Could not read the camera frame.")
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

    network_threshold, per_class = _resolve_class_thresholds(
        labels, threshold, class_thresholds
    )

    height, width = frame.shape[:2]
    # Strokes were tuned on the native frame. Scale them up when the painted
    # bitmap is larger. A crop is pasted back 1:1, so its reference shrinks
    # with the crop and the labels keep the full-frame weight.
    native_side = infer_frame_max_side(meta, "accurate")
    clipped: dict[str, int] | None = None
    work = frame
    if roi:
        mapped = {
            "x": int(roi.get("x", 0)),
            "y": int(roi.get("y", 0)),
            "w": int(roi.get("w", 0)),
            "h": int(roi.get("h", 0)),
        }
        # ROI is stored in full camera pixels; infer JPEGs are often downscaled.
        if isinstance(roi_space, dict):
            src_w = int(roi_space.get("width") or 0)
            src_h = int(roi_space.get("height") or 0)
            if src_w > 0 and src_h > 0 and (src_w != width or src_h != height):
                mapped = scale_roi_to_frame(mapped, src_w, src_h, width, height)
        clipped = clip_roi_to_frame(mapped, width, height)
        if clipped is None:
            raise RuntimeError("The region of interest does not fit this frame.")
        x1 = clipped["x"]
        y1 = clipped["y"]
        x2 = x1 + clipped["w"]
        y2 = y1 + clipped["h"]
        work = frame[y1:y2, x1:x2]

    full_long = max(height, width, 1)
    work_long = max(int(work.shape[0]), int(work.shape[1]), 1)
    reference_side = max(1, int(round(native_side * work_long / float(full_long))))

    instances: list[dict[str, Any]] = []
    if family == "hailo":
        from hailo_runtime import run_hailo  # noqa: PLC0415

        annotated, items, class_areas, instances = run_hailo(
            model,
            work,
            network_threshold,
            labels,
            task,
            draw=draw,
            reference_side=reference_side,
            class_thresholds=per_class,
        )
    elif family == "ultralytics":
        infer_size = yolo_infer_size(int(meta.get("resolution") or 640), speed_key)
        annotated, items, class_areas, instances = _run_ultralytics(
            model,
            work,
            network_threshold,
            labels,
            infer_size,
            task,
            draw=draw,
            reference_side=reference_side,
            class_thresholds=per_class,
        )
    else:
        annotated, items, class_areas, instances = _run_model(
            model,
            work,
            network_threshold,
            labels,
            infer_size=SPEED_SIZES[speed_key],
            draw=draw,
            reference_side=reference_side,
            class_thresholds=per_class,
        )

    image = None
    if draw:
        if clipped is not None:
            composed = frame.copy()
            x1 = clipped["x"]
            y1 = clipped["y"]
            x2 = x1 + clipped["w"]
            y2 = y1 + clipped["h"]
            if annotated.shape[0] == clipped["h"] and annotated.shape[1] == clipped["w"]:
                composed[y1:y2, x1:x2] = annotated
            else:
                resized = cv2.resize(
                    annotated,
                    (clipped["w"], clipped["h"]),
                    interpolation=cv2.INTER_LINEAR,
                )
                composed[y1:y2, x1:x2] = resized
            draw_roi_rectangle(composed, clipped, native_side)
            out = composed
        else:
            out = annotated
        ok, buf = cv2.imencode(".jpg", out, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if not ok:
            raise RuntimeError("Could not encode the result image.")
        image = "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii")
    result_mode = (_TASKS.get(task) or _TASKS["segment"])["result_mode"]
    best = items[0] if items else None
    best_name = str(best.get("class") or "") if best else ""
    best_cut = per_class.get(best_name, network_threshold) if best_name else network_threshold
    match = best if best and float(best.get("confidence") or 0) >= best_cut else None
    counts: dict[str, int] = {}
    if result_mode == "counts":
        for item in items:
            counts[item["class"]] = counts.get(item["class"], 0) + 1
    measurements = _build_measurements(labels, counts, class_areas, result_mode)
    pecan_samples = empty_frame_samples()
    if pecan_classes_ready(labels) and instances:
        if clipped is not None:
            bounds = (
                0,
                0,
                int(clipped["w"]),
                int(clipped["h"]),
            )
            sample_shape = (int(work.shape[0]), int(work.shape[1]))
        else:
            bounds = (0, 0, width, height)
            sample_shape = (height, width)
        pecan_samples = collect_frame_samples(
            shape=sample_shape,
            bounds=bounds,
            instances=instances,
        )
        pecan_samples = {
            "whole_pecan_areas": [float(v) for v in pecan_samples.get("whole_pecan_areas") or []],
            "broken_kernel_area": float(pecan_samples.get("broken_kernel_area") or 0),
            "exposed_kernel_ratios": [
                float(v) for v in pecan_samples.get("exposed_kernel_ratios") or []
            ],
            "n": int(pecan_samples.get("n") or 0),
            "has_masks": bool(pecan_samples.get("has_masks")),
        }
    # Time the whole sample, including overlay encode and pecan KPI work, so
    # Deploy's rate cap matches what the Pi actually spent.
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    payload: dict[str, Any] = {
        "ok": True,
        "elapsed_ms": elapsed_ms,
        "threshold": network_threshold,
        "class_thresholds": dict(per_class),
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
        "pecan_samples": pecan_samples,
    }
    if clipped is not None:
        payload["roi"] = clipped
    return payload


def load_model(path: Path, meta: dict[str, Any]) -> Any:
    return _load_model(path, meta)


def predict_roi_frame(
    model: Any,
    jpeg: bytes,
    meta: dict[str, Any],
    *,
    roi: dict[str, int] | None,
    thresholds: dict[str, float],
    min_area_px: float = MIN_AREA_PX,
    model_size: int = MODEL_SIZE,
    draw: bool = True,
    roi_space: dict[str, Any] | None = None,
    full_frame: bool = False,
) -> dict[str, Any]:
    """Crop ROI, run YOLO at fixed size, return model-space half/piece areas."""
    frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise RuntimeError("Could not read the video frame.")
    started = time.perf_counter()
    family = str(meta.get("family") or "ultralytics")
    if family != "ultralytics":
        raise RuntimeError("Half-yield needs a YOLO segmentation model.")
    task = str(meta.get("task") or "segment")
    if task not in {"segment", "semantic"}:
        raise RuntimeError("Half-yield needs a segmentation model.")

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

    height, width = frame.shape[:2]
    if full_frame or not roi:
        clipped = {"x": 0, "y": 0, "w": int(width), "h": int(height)}
    else:
        mapped = {
            "x": int(roi.get("x", 0)),
            "y": int(roi.get("y", 0)),
            "w": int(roi.get("w", 0)),
            "h": int(roi.get("h", 0)),
        }
        if isinstance(roi_space, dict):
            src_w = int(roi_space.get("width") or 0)
            src_h = int(roi_space.get("height") or 0)
            if src_w > 0 and src_h > 0 and (src_w != width or src_h != height):
                mapped = scale_roi_to_frame(mapped, src_w, src_h, width, height)
        clipped = clip_roi_to_frame(mapped, width, height)
    if clipped is None:
        raise RuntimeError("The measurement region does not fit this frame.")

    x1 = clipped["x"]
    y1 = clipped["y"]
    x2 = x1 + clipped["w"]
    y2 = y1 + clipped["h"]
    crop = frame[y1:y2, x1:x2]
    resized = cv2.resize(crop, (int(model_size), int(model_size)), interpolation=cv2.INTER_LINEAR)

    class_thresh = {
        "half": float(thresholds.get("half", 0.30)),
        "piece": float(thresholds.get("piece", 0.30)),
        "obscured": float(thresholds.get("obscured", 0.30)),
        "shell": float(thresholds.get("shell", 0.30)),
    }
    base_conf = min(class_thresh.values())
    results = model.predict(
        resized,
        imgsz=int(model_size),
        device="cpu",
        verbose=False,
        stream=False,
        conf=float(base_conf),
    )
    result = results[0] if isinstance(results, list) and results else results
    if result is None:
        raise RuntimeError("The model did not return a result.")

    ids = class_ids_for(labels)
    half_ids = ids["half"]
    piece_ids = ids["piece"]
    obscured_ids = ids["obscured"]
    shell_ids = ids["shell"]

    polygons: list[tuple[np.ndarray, int]] = []
    half_area_sum = 0.0
    piece_area_sum = 0.0
    items: list[dict[str, Any]] = []

    has_masks = getattr(result, "masks", None) is not None and getattr(result.masks, "xy", None) is not None
    has_boxes = getattr(result, "boxes", None) is not None
    if has_masks and has_boxes and len(result.masks.xy) and len(result.boxes.cls):
        classes = _as_numpy(result.boxes.cls).astype(int).reshape(-1)
        confs = _as_numpy(result.boxes.conf).astype(float).reshape(-1)
        thresh = np.full_like(confs, float(base_conf), dtype=float)
        if half_ids:
            thresh[np.isin(classes, list(half_ids))] = class_thresh["half"]
        if piece_ids:
            thresh[np.isin(classes, list(piece_ids))] = class_thresh["piece"]
        if obscured_ids:
            thresh[np.isin(classes, list(obscured_ids))] = class_thresh["obscured"]
        if shell_ids:
            thresh[np.isin(classes, list(shell_ids))] = class_thresh["shell"]
        keep = np.where(confs >= thresh)[0]
        sx = clipped["w"] / float(model_size)
        sy = clipped["h"] / float(model_size)
        for index in keep:
            seg_xy = result.masks.xy[int(index)]
            if seg_xy is None or len(seg_xy) == 0:
                continue
            try:
                mask = result.masks.data[int(index)]
                area_i = float((mask > 0.5).sum().item())
            except Exception:  # noqa: BLE001
                contour = np.asarray(seg_xy, np.float32).reshape(-1, 1, 2)
                area_i = float(abs(cv2.contourArea(contour)))
            class_id = int(classes[int(index)])
            confidence = float(confs[int(index)])
            if area_i >= float(min_area_px):
                if class_id in half_ids:
                    half_area_sum += area_i
                if class_id in piece_ids:
                    piece_area_sum += area_i
            mapped = np.asarray(seg_xy, dtype=np.float32).copy()
            mapped[:, 0] = mapped[:, 0] * sx + x1
            mapped[:, 1] = mapped[:, 1] * sy + y1
            polygons.append((mapped, class_id))
            name = labels[class_id] if 0 <= class_id < len(labels) else f"Class {class_id + 1}"
            items.append(
                {
                    "class": name,
                    "confidence": round(confidence, 3),
                    "area": round(area_i, 1),
                }
            )

    annotated = frame.copy() if draw else frame
    if draw:
        draw_roi_rectangle(annotated, clipped)
        id_to_name = {index: name for index, name in enumerate(labels)}
        draw_inspection_polygons(annotated, polygons, id_to_name)

    image = None
    if draw:
        ok, buf = cv2.imencode(".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            raise RuntimeError("Could not encode the result image.")
        image = "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii")

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    items.sort(key=lambda item: item["confidence"], reverse=True)
    return {
        "ok": True,
        "elapsed_ms": elapsed_ms,
        "half_area": float(half_area_sum),
        "piece_area": float(piece_area_sum),
        "roi": clipped,
        "items": items[:40],
        "image": image,
        "task": task,
        "result_mode": "counts",
    }

