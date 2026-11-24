"""Overlay drawing and measurement helpers. No torch import."""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from segment_inspect import class_color_bgr

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


def mark_scale(frame: np.ndarray) -> float:
    """Size of chips and outlines relative to a 1280 px result frame."""
    long_side = max(int(frame.shape[0]), int(frame.shape[1]), 1)
    return long_side / 1280.0


def mark_thickness(frame: np.ndarray) -> int:
    return max(1, int(round(2 * mark_scale(frame))))


def draw_detection_label(
    frame: np.ndarray,
    text: str,
    origin: tuple[int, int],
    color: tuple[int, int, int],
    reference_side: int | None = None,
    *,
    font: float = 0.48,
) -> None:
    """Class-color chip with white text, centered on the top of the object."""
    del reference_side
    scale = mark_scale(frame)
    font_scale = font * scale
    text_thickness = 1
    (text_w, text_h), baseline = cv2.getTextSize(
        text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, text_thickness
    )
    pad_x = max(4, int(round(6 * scale)))
    pad_y = max(2, int(round(3 * scale)))
    plate_w = text_w + pad_x * 2
    plate_h = text_h + baseline + pad_y * 2
    height, width = frame.shape[:2]
    left = int(origin[0]) - plate_w // 2
    top = int(origin[1]) - plate_h
    left = min(max(0, left), max(0, width - plate_w))
    if top < 0:
        top = 0
    if top + plate_h > height:
        top = max(0, height - plate_h)
    cv2.rectangle(
        frame,
        (left, top),
        (left + plate_w, top + plate_h),
        color,
        -1,
        cv2.LINE_AA,
    )
    cv2.putText(
        frame,
        text,
        (left + pad_x, top + pad_y + text_h),
        cv2.FONT_HERSHEY_SIMPLEX,
        font_scale,
        (255, 255, 255),
        text_thickness,
        cv2.LINE_AA,
    )


# If this fraction of the smaller detection sits inside the other, they are the
# same object. The higher confidence keeps the label.
# kernel_region is allowed to share an object with pecan_open or pecan_crushed.
# The cross-class overlap filter is off. Same-class duplicates still collapse,
# and stray kernel_region labels are still dropped.
SUPPRESS_OVERLAPS = False
SAME_OBJECT_OVERLAP = 0.5
# A kernel_region may sit this far from pecan_open or pecan_crushed, as a
# fraction of the kernel's shorter side.
KERNEL_NEIGHBOR_FRACTION = 0.2
_KERNEL_HOSTS = frozenset({"pecan_open", "pecan_crushed"})
# A more specific kernel label replaces kernel_region on the same meat.
_KERNEL_SPECIFIC = frozenset({"kernel_broken", "kernel_half"})
_KERNEL_OVERLAP_PAIRS = (
    frozenset({"kernel_region", "pecan_open"}),
    frozenset({"kernel_region", "pecan_crushed"}),
)


def _box_overlap_coefficient(
    box_a: tuple[float, float, float, float],
    box_b: tuple[float, float, float, float],
) -> float:
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    smaller = min(area_a, area_b)
    if smaller <= 0:
        return 0.0
    return inter / smaller


def _polygon_overlap_coefficient(poly_a: np.ndarray, poly_b: np.ndarray) -> float:
    pts_a = np.asarray(poly_a, dtype=np.int32).reshape(-1, 2)
    pts_b = np.asarray(poly_b, dtype=np.int32).reshape(-1, 2)
    if len(pts_a) < 3 or len(pts_b) < 3:
        return 0.0
    min_x = int(min(pts_a[:, 0].min(), pts_b[:, 0].min()))
    min_y = int(min(pts_a[:, 1].min(), pts_b[:, 1].min()))
    max_x = int(max(pts_a[:, 0].max(), pts_b[:, 0].max())) + 1
    max_y = int(max(pts_a[:, 1].max(), pts_b[:, 1].max())) + 1
    height = max(1, max_y - min_y)
    width = max(1, max_x - min_x)
    canvas_a = np.zeros((height, width), dtype=np.uint8)
    canvas_b = np.zeros((height, width), dtype=np.uint8)
    shift = np.array([min_x, min_y], dtype=np.int32)
    cv2.fillPoly(canvas_a, [(pts_a - shift).reshape(-1, 1, 2)], 1)
    cv2.fillPoly(canvas_b, [(pts_b - shift).reshape(-1, 1, 2)], 1)
    area_a = int(canvas_a.sum())
    area_b = int(canvas_b.sum())
    smaller = min(area_a, area_b)
    if smaller <= 0:
        return 0.0
    return int(np.logical_and(canvas_a, canvas_b).sum()) / float(smaller)


def _mask_overlap_coefficient(mask_a: np.ndarray, mask_b: np.ndarray) -> float:
    area_a = int(np.asarray(mask_a).astype(bool).sum())
    area_b = int(np.asarray(mask_b).astype(bool).sum())
    smaller = min(area_a, area_b)
    if smaller <= 0:
        return 0.0
    inter = int(np.logical_and(mask_a, mask_b).sum())
    return inter / float(smaller)


def _detection_overlap(left: dict[str, Any], right: dict[str, Any]) -> float:
    mask_a = left.get("mask")
    mask_b = right.get("mask")
    if mask_a is not None and mask_b is not None:
        return _mask_overlap_coefficient(mask_a, mask_b)
    poly_a = left.get("polygon")
    poly_b = right.get("polygon")
    if poly_a is not None and poly_b is not None:
        return _polygon_overlap_coefficient(poly_a, poly_b)
    return _box_overlap_coefficient(tuple(left["box"]), tuple(right["box"]))


def _overlap_allowed(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """kernel_region may sit on pecan_open or pecan_crushed. Other pairs may not."""
    pair = frozenset(
        {
            str(left.get("class") or "").strip(),
            str(right.get("class") or "").strip(),
        }
    )
    return pair in _KERNEL_OVERLAP_PAIRS


def center_inside_roi(
    box: tuple[float, float, float, float] | list[float],
    roi: dict[str, int],
) -> bool:
    """True when the box center lies inside the ROI, edges included on the top-left."""
    cx = (float(box[0]) + float(box[2])) / 2.0
    cy = (float(box[1]) + float(box[3])) / 2.0
    left = float(roi["x"])
    top = float(roi["y"])
    return left <= cx < left + float(roi["w"]) and top <= cy < top + float(roi["h"])


def _box_of(record: dict[str, Any]) -> tuple[float, float, float, float]:
    box = record.get("box") or (0.0, 0.0, 0.0, 0.0)
    return (float(box[0]), float(box[1]), float(box[2]), float(box[3]))


def _box_gap(
    box_a: tuple[float, float, float, float],
    box_b: tuple[float, float, float, float],
) -> float:
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b
    gap_x = 0.0
    if ax2 < bx1:
        gap_x = bx1 - ax2
    elif bx2 < ax1:
        gap_x = ax1 - bx2
    gap_y = 0.0
    if ay2 < by1:
        gap_y = by1 - ay2
    elif by2 < ay1:
        gap_y = ay1 - by2
    return (gap_x * gap_x + gap_y * gap_y) ** 0.5


def _regions_within(kernel: dict[str, Any], host: dict[str, Any], gap: float) -> bool:
    """True when the outlines overlap or the clear gap is within `gap` pixels."""
    radius = max(1, int(round(gap)))
    mask_k = kernel.get("mask")
    mask_h = host.get("mask")
    if mask_k is not None and mask_h is not None:
        kernel_px = np.asarray(mask_k).astype(bool)
        host_px = np.asarray(mask_h).astype(bool)
        if kernel_px.shape != host_px.shape:
            return _box_gap(_box_of(kernel), _box_of(host)) <= gap
        element = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
        grown = cv2.dilate(host_px.astype(np.uint8), element).astype(bool)
        return bool(np.logical_and(grown, kernel_px).any())
    poly_k = kernel.get("polygon")
    poly_h = host.get("polygon")
    if poly_k is not None and poly_h is not None:
        pts_k = np.asarray(poly_k, dtype=np.int32).reshape(-1, 2)
        pts_h = np.asarray(poly_h, dtype=np.int32).reshape(-1, 2)
        if len(pts_k) < 3 or len(pts_h) < 3:
            return _box_gap(_box_of(kernel), _box_of(host)) <= gap
        min_x = int(min(pts_k[:, 0].min(), pts_h[:, 0].min())) - radius
        min_y = int(min(pts_k[:, 1].min(), pts_h[:, 1].min())) - radius
        max_x = int(max(pts_k[:, 0].max(), pts_h[:, 0].max())) + radius + 1
        max_y = int(max(pts_k[:, 1].max(), pts_h[:, 1].max())) + radius + 1
        height = max(1, max_y - min_y)
        width = max(1, max_x - min_x)
        shift = np.array([min_x, min_y], dtype=np.int32)
        host_canvas = np.zeros((height, width), dtype=np.uint8)
        kernel_canvas = np.zeros((height, width), dtype=np.uint8)
        cv2.fillPoly(host_canvas, [(pts_h - shift).reshape(-1, 1, 2)], 1)
        cv2.fillPoly(kernel_canvas, [(pts_k - shift).reshape(-1, 1, 2)], 1)
        element = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
        grown = cv2.dilate(host_canvas, element)
        return bool(np.logical_and(grown, kernel_canvas).any())
    return _box_gap(_box_of(kernel), _box_of(host)) <= gap


def _keep_supported_kernels(records: list[dict[str, Any]], flags: list[bool]) -> list[bool]:
    """Drop kernel_region unless it overlaps or tightly neighbors an open or crushed pecan."""
    hosts = [
        index
        for index, record in enumerate(records)
        if flags[index] and str(record.get("class") or "").strip() in _KERNEL_HOSTS
    ]
    kept = list(flags)
    for index, record in enumerate(records):
        if not flags[index] or str(record.get("class") or "").strip() != "kernel_region":
            continue
        box = _box_of(record)
        shorter = max(1.0, min(abs(box[2] - box[0]), abs(box[3] - box[1])))
        gap = KERNEL_NEIGHBOR_FRACTION * shorter
        if not any(
            _box_gap(box, _box_of(records[host])) <= gap and _regions_within(record, records[host], gap)
            for host in hosts
        ):
            kept[index] = False
    return kept


def _drop_kernels_with_specific_label(
    records: list[dict[str, Any]],
    flags: list[bool],
    overlap: float,
) -> list[bool]:
    """Drop kernel_region when half of the smaller outline sits inside kernel_half or kernel_broken."""
    specific = [
        index
        for index, record in enumerate(records)
        if flags[index] and str(record.get("class") or "").strip() in _KERNEL_SPECIFIC
    ]
    kept = list(flags)
    for index, record in enumerate(records):
        if not flags[index] or str(record.get("class") or "").strip() != "kernel_region":
            continue
        if any(_detection_overlap(record, records[other]) >= overlap for other in specific):
            kept[index] = False
    return kept


def _drop_same_class_overlaps(
    records: list[dict[str, Any]],
    flags: list[bool],
    overlap: float,
) -> list[bool]:
    """Keep the higher-confidence detection when two of the same class cover one object."""
    order = sorted(
        [index for index, flag in enumerate(flags) if flag],
        key=lambda index: float(records[index].get("confidence") or 0.0),
        reverse=True,
    )
    kept: list[int] = []
    out = list(flags)
    for index in order:
        name = str(records[index].get("class") or "").strip()
        if any(
            str(records[other].get("class") or "").strip() == name
            and _detection_overlap(records[index], records[other]) >= overlap
            for other in kept
        ):
            out[index] = False
            continue
        kept.append(index)
    return out


def dominant_keep(records: list[dict[str, Any]], overlap: float = SAME_OBJECT_OVERLAP) -> list[bool]:
    """Keep the highest-confidence detection when two cover the same object."""
    if not SUPPRESS_OVERLAPS:
        flags = [True] * len(records)
    else:
        order = sorted(
            range(len(records)),
            key=lambda index: float(records[index].get("confidence") or 0.0),
            reverse=True,
        )
        kept: list[int] = []
        for index in order:
            if any(
                not _overlap_allowed(records[index], records[other])
                and _detection_overlap(records[index], records[other]) >= overlap
                for other in kept
            ):
                continue
            kept.append(index)
        flags = [False] * len(records)
        for index in kept:
            flags[index] = True
    # Same-class duplicates always collapse, even when the cross-class filter is off.
    flags = _drop_same_class_overlaps(records, flags, overlap)
    flags = _keep_supported_kernels(records, flags)
    return _drop_kernels_with_specific_label(records, flags, overlap)


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
    color = class_color_bgr(class_id)
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
        overlay[region] = class_color_bgr(class_id)
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
    class_thresholds: dict[str, float] | None = None,
    default_threshold: float = 0.5,
    roi: dict[str, int] | None = None,
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
    items: list[dict[str, Any]] = []
    instances: list[dict[str, Any]] = []
    pending: list[tuple[int, int, float, str, tuple[int, int, int], tuple[int, int]]] = []
    polygons_by_class: dict[str, list[np.ndarray]] = {}
    for index, class_id in enumerate(class_ids):
        class_id = int(class_id)
        confidence = float(confidences[index]) if index < len(confidences) else 0.0
        name = labels[class_id] if 0 <= class_id < len(labels) else f"Class {class_id + 1}"
        cutoff = float(default_threshold)
        if class_thresholds and name in class_thresholds:
            cutoff = float(class_thresholds[name])
        if confidence < cutoff:
            continue
        color = class_color_bgr(class_id)
        polygon = None
        if polygons is not None and index < len(polygons):
            pts = np.asarray(polygons[index], dtype=np.int32)
            if pts.ndim == 2 and len(pts) >= 3:
                polygon = pts
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
        pending.append((index, class_id, confidence, name, color, anchor))
        items.append({"class": name, "confidence": round(confidence, 3)})
    if roi is not None:
        kept_rows = [
            (inst, pend, item)
            for inst, pend, item in zip(instances, pending, items)
            if center_inside_roi(inst.get("box") or (0.0, 0.0, 0.0, 0.0), roi)
        ]
        instances = [row[0] for row in kept_rows]
        pending = [row[1] for row in kept_rows]
        items = [row[2] for row in kept_rows]
    keep_flags = dominant_keep(
        [
            {
                "class": inst.get("class") or "",
                "confidence": inst.get("confidence") or 0.0,
                "box": inst.get("box") or (0.0, 0.0, 0.0, 0.0),
                "polygon": inst.get("polygon"),
            }
            for inst in instances
        ]
    )
    pending = [row for row, flag in zip(pending, keep_flags) if flag]
    items = [row for row, flag in zip(items, keep_flags) if flag]
    instances = [row for row, flag in zip(instances, keep_flags) if flag]
    polygons_by_class = {}
    for inst in instances:
        poly = inst.get("polygon")
        if poly is None:
            continue
        polygons_by_class.setdefault(str(inst["class"]), []).append(np.asarray(poly, dtype=np.int32))
    if draw:
        thickness = mark_thickness(annotated)
        point_radius = max(2, int(round(3 * mark_scale(annotated))))
        for index, _class_id, _confidence, name, color, anchor in pending:
            label_at = anchor
            drew_outline = False
            if polygons is not None and index < len(polygons):
                pts = np.asarray(polygons[index], dtype=np.int32)
                if pts.ndim == 2 and len(pts) >= 3:
                    cv2.polylines(
                        annotated, [pts.reshape(-1, 1, 2)], True, color, thickness, cv2.LINE_AA
                    )
                    drew_outline = True
                    label_at = (
                        int(round((float(pts[:, 0].min()) + float(pts[:, 0].max())) / 2.0)),
                        int(round(float(pts[:, 1].min()))),
                    )
            if not drew_outline and corners is not None and index < len(corners):
                pts = np.asarray(corners[index], dtype=np.int32).reshape(-1, 2)
                cv2.polylines(
                    annotated, [pts.reshape(-1, 1, 2)], True, color, thickness, cv2.LINE_AA
                )
                drew_outline = True
                label_at = (
                    int(round((float(pts[:, 0].min()) + float(pts[:, 0].max())) / 2.0)),
                    int(round(float(pts[:, 1].min()))),
                )
            if not drew_outline and boxes is not None and index < len(boxes):
                x1, y1, x2, y2 = (int(round(float(v))) for v in boxes[index][:4])
                cv2.rectangle(annotated, (x1, y1), (x2, y2), color, thickness, cv2.LINE_AA)
                label_at = ((x1 + x2) // 2, y1)
            if keypoints is not None and index < len(keypoints):
                for point in np.asarray(keypoints[index]).reshape(-1, 2):
                    x_pos, y_pos = int(round(float(point[0]))), int(round(float(point[1])))
                    if x_pos > 0 or y_pos > 0:
                        cv2.circle(annotated, (x_pos, y_pos), point_radius, color, -1)
            draw_detection_label(annotated, name, label_at, color, reference_side)
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


# White ROI outline. Class overlays use class_color_bgr.
_WHITE_BGR = (255, 255, 255)


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
    """Class-colored outline and label chip, same as the other models."""
    thickness = mark_thickness(frame)
    for polygon, class_id in polygons:
        points = np.round(polygon).astype(np.int32)
        if points.ndim != 2 or len(points) < 3:
            continue
        color = class_color_bgr(int(class_id))
        cv2.polylines(
            frame, [points.reshape(-1, 1, 2)], True, color, thickness, cv2.LINE_AA
        )
        label = str(names.get(int(class_id), class_id))
        label_at = (
            int(round((float(points[:, 0].min()) + float(points[:, 0].max())) / 2.0)),
            int(round(float(points[:, 1].min()))),
        )
        draw_detection_label(frame, label, label_at, color)

