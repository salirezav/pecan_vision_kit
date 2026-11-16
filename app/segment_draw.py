"""Overlay drawing and measurement helpers. No torch import."""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from segment_inspect import _COLORS

def overlay_scale(frame: np.ndarray, reference_side: int | None = None) -> float:
    """How much to grow strokes and type so they match a native-sized frame.

    The 2 px / 0.6 font constants were tuned on the model's native image.
    A larger display frame multiplies them by long_side / reference_side.
    Missing reference keeps the constants unchanged.
    """
    if reference_side is None:
        return 1.0
    long_side = max(int(frame.shape[0]), int(frame.shape[1]), 1)
    return long_side / float(max(int(reference_side), 1))


def overlay_thickness(
    frame: np.ndarray,
    reference_side: int | None = None,
    *,
    base: int = 2,
) -> int:
    return max(1, int(round(base * overlay_scale(frame, reference_side))))


def draw_detection_label(
    frame: np.ndarray,
    text: str,
    origin: tuple[int, int],
    color: tuple[int, int, int],
    reference_side: int | None = None,
    *,
    font: float = 0.6,
) -> None:
    scale = overlay_scale(frame, reference_side)
    thickness = max(1, int(round(2 * scale)))
    x_pos, y_pos = origin
    gap = max(1, int(round(6 * scale)))
    floor = max(thickness + 1, int(round(20 * scale)))
    cv2.putText(
        frame,
        text,
        (int(x_pos), max(floor, int(y_pos) - gap)),
        cv2.FONT_HERSHEY_SIMPLEX,
        font * scale,
        color,
        thickness,
        cv2.LINE_AA,
    )


def _as_numpy(value: Any) -> np.ndarray:
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    return np.asarray(value)

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
    reference_side: int | None = None,
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
    scale = overlay_scale(annotated, reference_side)
    bar_h = max(1, int(round(42 * scale)))
    cv2.rectangle(annotated, (0, 0), (annotated.shape[1], bar_h), color, -1)
    cv2.putText(
        annotated,
        f"{best['class']} {best['confidence']:.0%}",
        (max(1, int(round(12 * scale))), max(1, int(round(28 * scale)))),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8 * scale,
        (255, 255, 255),
        max(1, int(round(2 * scale))),
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
    *,
    draw: bool = True,
    reference_side: int | None = None,
) -> tuple[np.ndarray, list[dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
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
            return (frame.copy() if draw else frame), [], {}, []
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

    annotated = frame.copy() if draw else frame
    overlay = annotated.copy() if draw else None
    drew_mask = False
    items: list[dict[str, Any]] = []
    instances: list[dict[str, Any]] = []
    pending: list[tuple[int, float, str, tuple[int, int, int], tuple[int, int]]] = []
    polygons_by_class: dict[str, list[np.ndarray]] = {}
    for index, class_id in enumerate(class_ids):
        class_id = int(class_id)
        confidence = float(confidences[index]) if index < len(confidences) else 0.0
        name = labels[class_id] if 0 <= class_id < len(labels) else f"Class {class_id + 1}"
        color = _COLORS[class_id % len(_COLORS)]
        polygon = None
        if polygons is not None and index < len(polygons):
            pts = np.asarray(polygons[index], dtype=np.int32)
            if pts.ndim == 2 and len(pts) >= 3:
                polygon = pts
                if draw and overlay is not None:
                    cv2.fillPoly(overlay, [pts], color)
                    drew_mask = True
                polygons_by_class.setdefault(name, []).append(pts)
        if boxes is not None and index < len(boxes):
            box = (
                float(boxes[index][0]),
                float(boxes[index][1]),
                float(boxes[index][2]),
                float(boxes[index][3]),
            )
        elif polygon is not None:
            box = (
                float(polygon[:, 0].min()),
                float(polygon[:, 1].min()),
                float(polygon[:, 0].max()),
                float(polygon[:, 1].max()),
            )
        elif corners is not None and index < len(corners):
            pts = np.asarray(corners[index], dtype=np.float32).reshape(-1, 2)
            box = (
                float(pts[:, 0].min()),
                float(pts[:, 1].min()),
                float(pts[:, 0].max()),
                float(pts[:, 1].max()),
            )
        else:
            box = (0.0, 0.0, 0.0, 0.0)
        instance: dict[str, Any] = {"class": name, "box": box, "confidence": confidence}
        if polygon is not None:
            instance["polygon"] = polygon
            instance["area"] = float(abs(cv2.contourArea(np.asarray(polygon, dtype=np.float32))))
        else:
            instance["area"] = max(0.0, (box[2] - box[0]) * (box[3] - box[1]))
        instances.append(instance)
        anchor = _label_anchor(boxes, corners, index)
        pending.append((class_id, confidence, name, color, anchor))
        items.append({"class": name, "confidence": round(confidence, 3)})
    if draw and drew_mask and overlay is not None:
        annotated = cv2.addWeighted(overlay, 0.45, annotated, 0.55, 0)
    if draw:
        thickness = overlay_thickness(annotated, reference_side)
        point_radius = max(1, int(round(3 * overlay_scale(annotated, reference_side))))
        for index, (_class_id, confidence, name, color, anchor) in enumerate(pending):
            if corners is not None and index < len(corners):
                pts = np.asarray(corners[index], dtype=np.int32).reshape(-1, 2)
                cv2.polylines(annotated, [pts], True, color, thickness)
            elif boxes is not None and index < len(boxes):
                x1, y1, x2, y2 = (int(round(float(v))) for v in boxes[index][:4])
                cv2.rectangle(annotated, (x1, y1), (x2, y2), color, thickness)
            if keypoints is not None and index < len(keypoints):
                for point in np.asarray(keypoints[index]).reshape(-1, 2):
                    x_pos, y_pos = int(round(float(point[0]))), int(round(float(point[1])))
                    if x_pos > 0 or y_pos > 0:
                        cv2.circle(annotated, (x_pos, y_pos), point_radius, color, -1)
            draw_detection_label(
                annotated,
                f"{name} {confidence:.0%}",
                anchor,
                color,
                reference_side,
            )
    items.sort(key=lambda item: item["confidence"], reverse=True)
    class_areas = _union_polygon_areas(frame.shape[:2], polygons_by_class)
    return annotated, items, class_areas, instances

def _union_polygon_areas(
    shape: tuple[int, int],
    polygons_by_class: dict[str, list[np.ndarray]],
) -> dict[str, int]:
    if not polygons_by_class:
        return {}
    height, width = shape
    # Prefer a compact scratch canvas when polygons only cover a region of the frame.
    areas: dict[str, int] = {}
    for name, polys in polygons_by_class.items():
        if not polys:
            areas[name] = 0
            continue
        min_x = max(0, int(min(int(pts[:, 0].min()) for pts in polys)) - 1)
        min_y = max(0, int(min(int(pts[:, 1].min()) for pts in polys)) - 1)
        max_x = min(width, int(max(int(pts[:, 0].max()) for pts in polys)) + 2)
        max_y = min(height, int(max(int(pts[:, 1].max()) for pts in polys)) + 2)
        if max_x <= min_x or max_y <= min_y:
            areas[name] = 0
            continue
        scratch = np.zeros((max_y - min_y, max_x - min_x), dtype=np.uint8)
        shifted = [pts - np.array([[min_x, min_y]], dtype=pts.dtype) for pts in polys]
        cv2.fillPoly(scratch, shifted, 1)
        areas[name] = int(scratch.sum())
    return areas

def _label_anchor(boxes: np.ndarray | None, corners: np.ndarray | None, index: int) -> tuple[int, int]:
    if boxes is not None and index < len(boxes):
        return int(round(float(boxes[index][0]))), int(round(float(boxes[index][1])))
    if corners is not None and index < len(corners):
        pts = np.asarray(corners[index]).reshape(-1, 2)
        return int(np.min(pts[:, 0])), int(np.min(pts[:, 1]))
    return 8, 24


# UGA Olympic (BGR) and white for half-yield inspection overlays.
_OLYMPIC_BGR = (96, 78, 0)
_WHITE_BGR = (255, 255, 255)
_BLACK_BGR = (0, 0, 0)


def draw_roi_rectangle(
    frame: np.ndarray,
    roi: dict[str, int],
    reference_side: int | None = None,
) -> None:
    x1 = int(roi["x"])
    y1 = int(roi["y"])
    x2 = x1 + int(roi["w"])
    y2 = y1 + int(roi["h"])
    cv2.rectangle(
        frame,
        (x1, y1),
        (x2, y2),
        _WHITE_BGR,
        overlay_thickness(frame, reference_side, base=1),
    )


def draw_inspection_polygons(
    frame: np.ndarray,
    polygons: list[tuple[np.ndarray, int]],
    names: dict[int, str],
) -> None:
    """White outline + Olympic contour with a label plate (half-yield style)."""
    for polygon, class_id in polygons:
        points = np.round(polygon).astype(np.int32).reshape(-1, 1, 2)
        if len(points) < 3:
            continue
        cv2.polylines(frame, [points], True, _WHITE_BGR, 4, cv2.LINE_AA)
        cv2.polylines(frame, [points], True, _OLYMPIC_BGR, 2, cv2.LINE_AA)
        moments = cv2.moments(points)
        if moments["m00"] == 0:
            continue
        label = str(names.get(int(class_id), class_id))
        if len(label) > 28:
            label = label[:27] + "…"
        (text_w, text_h), baseline = cv2.getTextSize(
            label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1
        )
        width = text_w + 12
        height = text_h + baseline + 10
        x = min(max(0, int(moments["m10"] / moments["m00"])), max(0, frame.shape[1] - width))
        y = min(max(height, int(moments["m01"] / moments["m00"]) - 10), frame.shape[0] - 1)
        cv2.rectangle(frame, (x, y - height), (x + width, y), _WHITE_BGR, -1)
        cv2.putText(
            frame,
            label,
            (x + 6, y - 6),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            _BLACK_BGR,
            1,
            cv2.LINE_AA,
        )

