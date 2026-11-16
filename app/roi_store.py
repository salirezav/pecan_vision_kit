"""Persisted camera ROI for Model and Deploy. Cleared means full frame."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from segment_inspect import STORE_DIR
from yield_measure import clip_roi_to_frame

ROI_PATH = STORE_DIR / "roi.json"
MIN_SIDE = 32


def roi_path() -> Path:
    return ROI_PATH


def validate_roi(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        raise ValueError("Provide x, y, w, and h for the region.")
    try:
        rect = {
            "x": int(raw["x"]),
            "y": int(raw["y"]),
            "w": int(raw["w"]),
            "h": int(raw["h"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Provide x, y, w, and h for the region.") from exc
    if rect["w"] < MIN_SIDE or rect["h"] < MIN_SIDE:
        raise ValueError(f"The region must be at least {MIN_SIDE}×{MIN_SIDE} pixels.")
    if rect["x"] < 0 or rect["y"] < 0:
        raise ValueError("The region cannot start outside the frame.")
    return rect


def load_roi() -> dict[str, int] | None:
    path = ROI_PATH
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return validate_roi(data)
    except ValueError:
        return None


def save_roi(raw: Any, *, width: int | None = None, height: int | None = None) -> dict[str, int]:
    rect = validate_roi(raw)
    if width is not None and height is not None and width > 0 and height > 0:
        clipped = clip_roi_to_frame(rect, width, height)
        if clipped is None:
            raise ValueError("That region does not fit the camera frame.")
        if clipped["w"] < MIN_SIDE or clipped["h"] < MIN_SIDE:
            raise ValueError(f"The region must be at least {MIN_SIDE}×{MIN_SIDE} pixels.")
        rect = clipped
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    ROI_PATH.write_text(json.dumps(rect, indent=2) + "\n")
    return rect


def clear_roi() -> None:
    try:
        ROI_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def status(*, width: int | None = None, height: int | None = None) -> dict[str, Any]:
    rect = load_roi()
    if rect is not None and width is not None and height is not None and width > 0 and height > 0:
        clipped = clip_roi_to_frame(rect, width, height)
        rect = clipped
    return {
        "roi": rect,
        "width": width,
        "height": height,
        "full_frame": rect is None,
    }
