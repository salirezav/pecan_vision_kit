"""Built-in pecan KPIs that need per-instance geometry."""

from __future__ import annotations

import bisect
import math
from typing import Any

import cv2
import numpy as np

PECAN_WHOLE = frozenset(
    {"pecan_uncracked", "pecan_hairline", "pecan_open", "pecan_crushed"}
)
PECAN_REQUIRED = frozenset(
    {
        "pecan_uncracked",
        "pecan_hairline",
        "pecan_open",
        "pecan_crushed",
        "kernel_region",
        "kernel_broken",
    }
)
MIN_PECANS = 200
BUILTIN_IDS = (
    "pecan_exposed_kernel_mean",
    "pecan_exposed_kernel_median",
    "pecan_broken_kernel_area_per_100",
)
HALFYIELD_BUILTIN_ID = "halfyield_half_share"
HALFYIELD_BUILTIN_ID_LEGACY = "halfyield_mass_share"
HALFYIELD_BUILTIN_NAME = "Half yield"
HALFYIELD_BUILTIN_FORMULA = "half / (half + piece)"
ALL_BUILTIN_IDS = (*BUILTIN_IDS, HALFYIELD_BUILTIN_ID)
BUILTIN_NAMES = {
    "pecan_exposed_kernel_mean": "Exposed-kernel mean",
    "pecan_exposed_kernel_median": "Exposed-kernel median",
    "pecan_broken_kernel_area_per_100": "Broken-kernel area per 100 pecans",
    HALFYIELD_BUILTIN_ID: HALFYIELD_BUILTIN_NAME,
}


def default_builtin_enabled() -> dict[str, bool]:
    return {str(mid): True for mid in ALL_BUILTIN_IDS}


def coalesce_builtin_enabled(raw: dict[str, Any] | None) -> dict[str, bool]:
    """Merge saved flags onto defaults; map legacy half-yield id to the current one."""
    builtins = default_builtin_enabled()
    if not isinstance(raw, dict):
        return builtins
    for mid in ALL_BUILTIN_IDS:
        if mid in raw:
            builtins[str(mid)] = raw.get(mid) is not False
        elif mid == HALFYIELD_BUILTIN_ID and HALFYIELD_BUILTIN_ID_LEGACY in raw:
            builtins[str(mid)] = raw.get(HALFYIELD_BUILTIN_ID_LEGACY) is not False
    return builtins


def halfyield_builtin_metric(*, enabled: bool = True) -> dict[str, Any]:
    return {
        "id": HALFYIELD_BUILTIN_ID,
        "name": HALFYIELD_BUILTIN_NAME,
        "formula": HALFYIELD_BUILTIN_FORMULA,
        "enabled": bool(enabled),
    }


def pecan_classes_ready(class_names: list[str] | None) -> bool:
    names = {str(name) for name in (class_names or []) if str(name).strip()}
    return PECAN_REQUIRED.issubset(names)


def empty_frame_samples() -> dict[str, Any]:
    return {
        "whole_pecan_areas": [],
        "broken_kernel_area": 0.0,
        "exposed_kernel_ratios": [],
        "n": 0,
        "has_masks": False,
        "exposed_sum": 0.0,
        "exposed_sum_sq": 0.0,
        "areas_sorted": [],
        "exposed_sorted": [],
    }


def touches_border(
    box: tuple[float, float, float, float],
    bounds: tuple[int, int, int, int],
    *,
    margin: float = 1.0,
) -> bool:
    """True when the axis-aligned box touches the measured region border."""
    x1, y1, x2, y2 = box
    left, top, right, bottom = bounds
    return (
        x1 <= left + margin
        or y1 <= top + margin
        or x2 >= right - margin
        or y2 >= bottom - margin
    )


def _boxes_overlap(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def _polygon_area(polygon: np.ndarray) -> float:
    pts = np.asarray(polygon, dtype=np.float32).reshape(-1, 2)
    if len(pts) < 3:
        return 0.0
    return float(abs(cv2.contourArea(pts)))


def _instance_area(item: dict[str, Any], box: tuple[float, float, float, float]) -> float:
    area = float(item.get("area") or 0)
    if area > 0:
        return area
    polygon = item.get("polygon")
    if polygon is not None:
        return _polygon_area(polygon)
    mask = item.get("mask")
    if mask is not None:
        return float(np.asarray(mask).astype(bool).sum())
    return max(0.0, (box[2] - box[0]) * (box[3] - box[1]))


def _crop_mask_in_roi(
    shape: tuple[int, int],
    item: dict[str, Any],
    roi: tuple[int, int, int, int],
) -> np.ndarray | None:
    """Rasterize or crop an instance mask into an ROI-sized bool array."""
    x0, y0, x1, y1 = roi
    height = y1 - y0
    width = x1 - x0
    if height <= 0 or width <= 0:
        return None
    mask = item.get("mask")
    if mask is not None:
        arr = np.asarray(mask)
        if arr.shape[:2] != shape:
            return None
        return arr[y0:y1, x0:x1].astype(bool)
    polygon = item.get("polygon")
    if polygon is None:
        return None
    pts = np.asarray(polygon, dtype=np.int32).reshape(-1, 2)
    if len(pts) < 3:
        return None
    shifted = pts - np.array([[x0, y0]], dtype=np.int32)
    canvas = np.zeros((height, width), dtype=np.uint8)
    cv2.fillPoly(canvas, [shifted.reshape(-1, 1, 2)], 1)
    return canvas.astype(bool)


def _clip_roi(
    shape: tuple[int, int],
    box: tuple[float, float, float, float],
    *,
    pad: int = 2,
) -> tuple[int, int, int, int] | None:
    height, width = shape
    x0 = max(0, int(math.floor(box[0])) - pad)
    y0 = max(0, int(math.floor(box[1])) - pad)
    x1 = min(width, int(math.ceil(box[2])) + pad)
    y1 = min(height, int(math.ceil(box[3])) + pad)
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)


def collect_frame_samples(
    *,
    shape: tuple[int, int],
    bounds: tuple[int, int, int, int],
    instances: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build per-frame pecan samples from instance dicts.

    Each instance: {class, box=(x1,y1,x2,y2), area?: float, polygon?: ndarray, mask?: ndarray}
    """
    whole_areas: list[float] = []
    exposed: list[float] = []
    broken_area = 0.0
    has_masks = False
    open_items: list[tuple[dict[str, Any], tuple[float, float, float, float]]] = []
    kernel_items: list[tuple[dict[str, Any], tuple[float, float, float, float]]] = []

    for item in instances:
        name = str(item.get("class") or "")
        box = item.get("box")
        if not box or len(box) < 4:
            continue
        box_t = (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
        if item.get("mask") is not None or item.get("polygon") is not None:
            has_masks = True
        area = _instance_area(item, box_t)

        if name == "kernel_broken":
            broken_area += area
            continue
        if name == "kernel_region":
            kernel_items.append((item, box_t))
            continue
        if name not in PECAN_WHOLE:
            continue
        if touches_border(box_t, bounds):
            continue
        whole_areas.append(area)
        if name == "pecan_open":
            open_items.append((item, box_t))

    if has_masks and open_items:
        for item, box_t in open_items:
            roi = _clip_roi(shape, box_t)
            if roi is None:
                continue
            pecan_mask = _crop_mask_in_roi(shape, item, roi)
            if pecan_mask is None or not pecan_mask.any():
                continue
            kernel_area = 0.0
            union = pecan_mask.copy()
            for kernel, kbox in kernel_items:
                if not _boxes_overlap(box_t, kbox):
                    continue
                kmask = _crop_mask_in_roi(shape, kernel, roi)
                if kmask is None:
                    continue
                overlap = np.logical_and(pecan_mask, kmask)
                if not overlap.any():
                    continue
                kernel_area += float(overlap.sum())
                union |= kmask
            union_area = float(union.sum())
            if union_area > 0:
                exposed.append(kernel_area / union_area)

    return {
        "whole_pecan_areas": whole_areas,
        "broken_kernel_area": float(broken_area),
        "exposed_kernel_ratios": exposed,
        "n": len(whole_areas),
        "has_masks": has_masks,
    }


def _ensure_run_stats(run: dict[str, Any]) -> None:
    """Fill running-stat fields when loading a legacy accumulator."""
    if "exposed_sum" not in run or "areas_sorted" not in run or "exposed_sorted" not in run:
        areas = [float(v) for v in (run.get("whole_pecan_areas") or []) if float(v) > 0]
        exposed = [float(v) for v in (run.get("exposed_kernel_ratios") or [])]
        run["areas_sorted"] = sorted(areas)
        run["exposed_sorted"] = sorted(exposed)
        run["exposed_sum"] = float(sum(exposed))
        run["exposed_sum_sq"] = float(sum(v * v for v in exposed))
    run.setdefault("exposed_sum", 0.0)
    run.setdefault("exposed_sum_sq", 0.0)
    run.setdefault("areas_sorted", [])
    run.setdefault("exposed_sorted", [])


def merge_run_samples(
    run: dict[str, Any] | None,
    frame: dict[str, Any] | None,
) -> dict[str, Any]:
    """Accumulate frame samples into a run accumulator (mutates run in place)."""
    base = run if isinstance(run, dict) else empty_frame_samples()
    if "whole_pecan_areas" not in base:
        base.update(empty_frame_samples())
    _ensure_run_stats(base)
    if not frame:
        return base
    areas = [float(v) for v in (frame.get("whole_pecan_areas") or [])]
    exposed = [float(v) for v in (frame.get("exposed_kernel_ratios") or [])]
    base["whole_pecan_areas"].extend(areas)
    base["broken_kernel_area"] = float(base.get("broken_kernel_area") or 0) + float(
        frame.get("broken_kernel_area") or 0
    )
    base["exposed_kernel_ratios"].extend(exposed)
    for value in areas:
        if value > 0:
            bisect.insort(base["areas_sorted"], value)
    for value in exposed:
        bisect.insort(base["exposed_sorted"], value)
        base["exposed_sum"] = float(base["exposed_sum"]) + value
        base["exposed_sum_sq"] = float(base["exposed_sum_sq"]) + value * value
    base["n"] = len(base["whole_pecan_areas"])
    base["has_masks"] = bool(base.get("has_masks")) or bool(frame.get("has_masks"))
    return base


def _median_sorted(ordered: list[float]) -> float | None:
    if not ordered:
        return None
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return float((ordered[mid - 1] + ordered[mid]) / 2.0)


def summarize_run(run: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Compute the three built-in KPI cards from accumulated samples."""
    run = run or empty_frame_samples()
    _ensure_run_stats(run)
    n = int(run.get("n") or 0)
    ready = n >= MIN_PECANS
    gate = {
        "ready": ready,
        "n": n,
        "needed": MIN_PECANS,
        "message": None if ready else f"insufficient data ({n} / {MIN_PECANS})",
    }
    areas_sorted = list(run.get("areas_sorted") or [])
    exposed_sorted = list(run.get("exposed_sorted") or [])
    exposed_count = len(exposed_sorted)
    has_masks = bool(run.get("has_masks"))

    mean_card = {
        "id": "pecan_exposed_kernel_mean",
        "name": BUILTIN_NAMES["pecan_exposed_kernel_mean"],
        **gate,
        "value": None,
        "ci95": None,
        "detail": None,
    }
    median_card = {
        "id": "pecan_exposed_kernel_median",
        "name": BUILTIN_NAMES["pecan_exposed_kernel_median"],
        **gate,
        "value": None,
        "ci95": None,
        "detail": None,
    }
    area_card = {
        "id": "pecan_broken_kernel_area_per_100",
        "name": BUILTIN_NAMES["pecan_broken_kernel_area_per_100"],
        **gate,
        "value": None,
        "ci95": None,
        "detail": None,
    }

    # Always compute a preview value when possible so Deploy charts move before
    # the N≥200 gate. `ready` still controls the insufficient-data label / CI.
    if not has_masks:
        mean_card["message"] = "Needs segmentation masks"
        mean_card["ready"] = False
        median_card["message"] = "Needs segmentation masks"
        median_card["ready"] = False
    elif exposed_count:
        mean_v = float(run["exposed_sum"]) / float(exposed_count)
        mean_card["value"] = mean_v
        median_card["value"] = _median_sorted(exposed_sorted)
        mean_card["detail"] = f"{exposed_count} open pecans · N={n}"
        median_card["detail"] = f"{exposed_count} open pecans · N={n}"
        if ready and exposed_count >= 2:
            sum_sq = float(run["exposed_sum_sq"])
            variance = (sum_sq - exposed_count * mean_v * mean_v) / (exposed_count - 1)
            variance = max(0.0, variance)
            se = math.sqrt(variance / exposed_count)
            half = 1.96 * se
            mean_card["ci95"] = [mean_v - half, mean_v + half]
        if not ready:
            mean_card["message"] = f"insufficient data ({n} / {MIN_PECANS})"
            median_card["message"] = f"insufficient data ({n} / {MIN_PECANS})"
    else:
        mean_card["message"] = (
            f"insufficient data ({n} / {MIN_PECANS})"
            if not ready
            else "No open pecans with kernel overlap"
        )
        mean_card["ready"] = False
        median_card["message"] = mean_card["message"]
        median_card["ready"] = False

    if areas_sorted and n > 0:
        median_area = _median_sorted(areas_sorted)
        if median_area and median_area > 0:
            broken = float(run.get("broken_kernel_area") or 0)
            area_card["value"] = 100.0 * (broken / median_area) / float(n)
            area_card["detail"] = (
                f"broken area {broken:.0f}px · median pecan {median_area:.0f}px · N={n}"
            )
            if not ready:
                area_card["message"] = f"insufficient data ({n} / {MIN_PECANS})"
        else:
            area_card["message"] = "Median pecan area is zero"
            area_card["ready"] = False
    else:
        area_card["message"] = (
            f"insufficient data ({n} / {MIN_PECANS})"
            if not ready
            else "No whole-pecan areas"
        )
        area_card["ready"] = False

    return {
        mean_card["id"]: mean_card,
        median_card["id"]: median_card,
        area_card["id"]: area_card,
    }
