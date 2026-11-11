"""Overlay drawing and measurement helpers. No torch import."""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from segment_inspect import _COLORS

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

