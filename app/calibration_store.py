"""Per-model class confidence and half-yield mass/area calibration.

Shared by Model and Deploy. Cleared keys fall back to defaults when read.
"""

from __future__ import annotations

import json
import math
import threading
from pathlib import Path
from typing import Any

from segment_inspect import STORE_DIR
from yield_measure import (
    CLASS_MATCHERS,
    class_ids_for,
    is_halfyield_classes,
)

CALIBRATION_PATH = STORE_DIR / "calibration.json"
DEFAULT_CLASS_CONFIDENCE = 0.50
DEFAULT_HALFYIELD_CONFIDENCE = 0.30
DEFAULT_MASS_PER_AREA = 0.001

_lock = threading.RLock()


def calibration_path() -> Path:
    return CALIBRATION_PATH


def _clamp01(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if not math.isfinite(number):
        number = float(default)
    return min(0.95, max(0.05, number))


def _clamp_mass(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if not math.isfinite(number):
        number = float(default)
    return min(1.0, max(1e-6, number))


def _read_all() -> dict[str, Any]:
    path = CALIBRATION_PATH
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_all(data: dict[str, Any]) -> None:
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    CALIBRATION_PATH.write_text(json.dumps(data, indent=2) + "\n")


def default_confidence_for(class_names: list[str], *, halfyield: bool | None = None) -> float:
    if halfyield is None:
        halfyield = is_halfyield_classes(class_names)
    return DEFAULT_HALFYIELD_CONFIDENCE if halfyield else DEFAULT_CLASS_CONFIDENCE


def empty_record(class_names: list[str]) -> dict[str, Any]:
    names = [str(name) for name in class_names if str(name).strip()]
    halfyield = is_halfyield_classes(names)
    default = default_confidence_for(names, halfyield=halfyield)
    record: dict[str, Any] = {
        "class_confidence": {name: default for name in names},
        "halfyield": halfyield,
    }
    if halfyield:
        record["half_mass_per_area_g_per_model_pixel2"] = DEFAULT_MASS_PER_AREA
        record["piece_mass_per_area_g_per_model_pixel2"] = DEFAULT_MASS_PER_AREA
    return record


def normalize_record(
    raw: Any,
    class_names: list[str],
) -> dict[str, Any]:
    names = [str(name) for name in class_names if str(name).strip()]
    halfyield = is_halfyield_classes(names)
    default = default_confidence_for(names, halfyield=halfyield)
    data = raw if isinstance(raw, dict) else {}
    stored = data.get("class_confidence") if isinstance(data.get("class_confidence"), dict) else {}
    class_confidence: dict[str, float] = {}
    for name in names:
        if name in stored:
            class_confidence[name] = _clamp01(stored.get(name), default)
        else:
            # Accept review-style keys like half_confidence for half-yield classes.
            legacy = data.get(f"{name}_confidence")
            if legacy is None and halfyield:
                for bucket, needles in CLASS_MATCHERS.items():
                    if any(needle in name.lower() for needle in needles):
                        legacy = data.get(f"{bucket}_confidence")
                        if legacy is None and isinstance(stored, dict):
                            legacy = stored.get(bucket)
                        break
            class_confidence[name] = _clamp01(legacy, default)
    record: dict[str, Any] = {
        "class_confidence": class_confidence,
        "halfyield": halfyield,
    }
    if halfyield:
        record["half_mass_per_area_g_per_model_pixel2"] = _clamp_mass(
            data.get("half_mass_per_area_g_per_model_pixel2"),
            DEFAULT_MASS_PER_AREA,
        )
        record["piece_mass_per_area_g_per_model_pixel2"] = _clamp_mass(
            data.get("piece_mass_per_area_g_per_model_pixel2"),
            DEFAULT_MASS_PER_AREA,
        )
    return record


def load(model_id: str, class_names: list[str]) -> dict[str, Any]:
    key = Path(str(model_id or "")).name
    if not key:
        return empty_record(class_names)
    with _lock:
        all_data = _read_all()
        raw = all_data.get(key)
    return normalize_record(raw, class_names)


def save(model_id: str, raw: Any, class_names: list[str]) -> dict[str, Any]:
    key = Path(str(model_id or "")).name
    if not key:
        raise ValueError("Choose a model before saving calibration.")
    record = normalize_record(raw, class_names)
    with _lock:
        all_data = _read_all()
        all_data[key] = {
            "class_confidence": dict(record["class_confidence"]),
        }
        if record.get("halfyield"):
            all_data[key]["half_mass_per_area_g_per_model_pixel2"] = float(
                record["half_mass_per_area_g_per_model_pixel2"]
            )
            all_data[key]["piece_mass_per_area_g_per_model_pixel2"] = float(
                record["piece_mass_per_area_g_per_model_pixel2"]
            )
        _write_all(all_data)
    return record


def class_thresholds(record: dict[str, Any], class_names: list[str]) -> dict[str, float]:
    """Map each class name to its confidence cutoff."""
    normalized = normalize_record(record, class_names)
    return {
        str(name): float(value)
        for name, value in (normalized.get("class_confidence") or {}).items()
    }


def bucket_thresholds(record: dict[str, Any], class_names: list[str]) -> dict[str, float]:
    """Half-yield predict_roi thresholds keyed by half/piece/obscured/shell."""
    normalized = normalize_record(record, class_names)
    conf = normalized.get("class_confidence") or {}
    ids = class_ids_for(class_names)
    out: dict[str, float] = {}
    default = DEFAULT_HALFYIELD_CONFIDENCE
    for bucket, class_ids in ids.items():
        values: list[float] = []
        for index in class_ids:
            if 0 <= index < len(class_names):
                name = str(class_names[index])
                if name in conf:
                    values.append(float(conf[name]))
        out[bucket] = min(values) if values else default
    return out


def mass_coefficients(record: dict[str, Any]) -> tuple[float, float]:
    data = record if isinstance(record, dict) else {}
    half = _clamp_mass(
        data.get("half_mass_per_area_g_per_model_pixel2"),
        DEFAULT_MASS_PER_AREA,
    )
    piece = _clamp_mass(
        data.get("piece_mass_per_area_g_per_model_pixel2"),
        DEFAULT_MASS_PER_AREA,
    )
    return half, piece
