"""Half-yield measurement helpers used by the Review page.

Reimplements the published HalfYield measurement rules (rolling area share,
mass estimate, CSV layout) without importing that project's source.
"""

from __future__ import annotations

import csv
import math
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_ROI = {"x": 400, "y": 300, "w": 500, "h": 600}
MODEL_SIZE = 640
MIN_AREA_PX = 30.0
DEFAULT_WINDOW_SEC = 60.0
DEFAULT_BIN_SEC = 10.0
SKIP_SECONDS = 5.0

CLASS_MATCHERS: dict[str, tuple[str, ...]] = {
    "half": ("half",),
    "piece": ("piece", "pieces"),
    "obscured": ("obscur",),
    "shell": ("shell",),
}


@dataclass
class Calibration:
    thresholds: dict[str, float] = field(
        default_factory=lambda: {
            "half": 0.30,
            "piece": 0.30,
            "obscured": 0.30,
            "shell": 0.30,
        }
    )
    half_mass_per_area: float = 0.001
    piece_mass_per_area: float = 0.001

    def mass_share(self, area_share: float | None) -> float | None:
        if area_share is None:
            return None
        half = float(area_share) * self.half_mass_per_area
        piece = (1.0 - float(area_share)) * self.piece_mass_per_area
        total = half + piece
        if total <= 0:
            return None
        return half / total

    def metadata(self) -> dict[str, float]:
        return {
            **{f"{name}_confidence": float(value) for name, value in self.thresholds.items()},
            "half_mass_per_area_g_per_model_pixel2": float(self.half_mass_per_area),
            "piece_mass_per_area_g_per_model_pixel2": float(self.piece_mass_per_area),
        }


def parse_calibration(raw: dict[str, Any] | None) -> Calibration:
    data = raw if isinstance(raw, dict) else {}
    thresholds = {
        "half": _clamp01(data.get("half_confidence"), 0.30),
        "piece": _clamp01(data.get("piece_confidence"), 0.30),
        "obscured": _clamp01(data.get("obscured_confidence"), 0.30),
        "shell": _clamp01(data.get("shell_confidence"), 0.30),
    }
    half = _clamp_mass(data.get("half_mass_per_area_g_per_model_pixel2"), 0.001)
    piece = _clamp_mass(data.get("piece_mass_per_area_g_per_model_pixel2"), 0.001)
    return Calibration(thresholds=thresholds, half_mass_per_area=half, piece_mass_per_area=piece)


def parse_roi(raw: dict[str, Any] | None) -> dict[str, int]:
    data = raw if isinstance(raw, dict) else {}
    roi = {
        "x": int(data.get("x", DEFAULT_ROI["x"])),
        "y": int(data.get("y", DEFAULT_ROI["y"])),
        "w": int(data.get("w", DEFAULT_ROI["w"])),
        "h": int(data.get("h", DEFAULT_ROI["h"])),
    }
    if roi["w"] < 32 or roi["h"] < 32:
        raise ValueError("The measurement region must be at least 32×32 pixels.")
    if roi["x"] < 0 or roi["y"] < 0:
        raise ValueError("The measurement region cannot start outside the frame.")
    return roi


def class_ids_for(names: list[str] | dict[Any, Any]) -> dict[str, set[int]]:
    if isinstance(names, dict):
        id_to_name = {int(key): str(value) for key, value in names.items()}
    else:
        id_to_name = {index: str(name) for index, name in enumerate(names)}
    out: dict[str, set[int]] = {key: set() for key in CLASS_MATCHERS}
    for class_id, name in id_to_name.items():
        lower = str(name).lower()
        for key, needles in CLASS_MATCHERS.items():
            if any(needle in lower for needle in needles):
                out[key].add(int(class_id))
    return out


def is_halfyield_classes(names: list[str] | dict[Any, Any] | None) -> bool:
    """True when the model has both half and piece classes (half-yield task)."""
    if not names:
        return False
    ids = class_ids_for(names)
    return bool(ids["half"] and ids["piece"])


def area_share(half_area: float, piece_area: float) -> float | None:
    total = float(half_area) + float(piece_area)
    if total <= 0:
        return None
    return float(half_area) / total


def integrated_area_totals(
    samples: list[tuple[float, float, float]],
) -> tuple[float, float]:
    """Time-integrate half and piece areas; returns (half_integral, piece_integral)."""
    if len(samples) < 2:
        return 0.0, 0.0
    half_integral = 0.0
    piece_integral = 0.0
    for index in range(len(samples) - 1):
        t0, half0, piece0 = samples[index]
        t1 = samples[index + 1][0]
        elapsed = t1 - t0
        if elapsed <= 0:
            continue
        half_integral += half0 * elapsed
        piece_integral += piece0 * elapsed
    return half_integral, piece_integral


def integrated_ratio_last(samples: list[tuple[float, float, float]], window_ms: float) -> float | None:
    """Integrate half/(half+piece) over the window using each interval's left sample."""
    if len(samples) < 2:
        return None
    cutoff = samples[-1][0] - float(window_ms)
    half_integral = 0.0
    total_integral = 0.0
    for index in range(len(samples) - 1):
        timestamp, half, piece = samples[index]
        next_timestamp = samples[index + 1][0]
        elapsed = next_timestamp - max(timestamp, cutoff)
        if elapsed <= 0:
            continue
        half_integral += half * elapsed
        total_integral += (half + piece) * elapsed
    if total_integral <= 0:
        return None
    return half_integral / total_integral


class YieldHistory:
    def __init__(self, window_ms: float) -> None:
        self.window_ms = float(window_ms)
        self.samples: deque[tuple[float, float, float]] = deque()
        self.points: deque[tuple[float, float | None]] = deque()

    @property
    def current(self) -> float | None:
        return self.points[-1][1] if self.points else None

    def append(self, timestamp: float, half_area: float, piece_area: float) -> bool:
        if not all(math.isfinite(value) for value in (timestamp, half_area, piece_area)):
            return False
        if self.samples and timestamp <= self.samples[-1][0]:
            return False
        self.samples.append((float(timestamp), float(half_area), float(piece_area)))
        cutoff = timestamp - self.window_ms
        while len(self.samples) > 1 and self.samples[1][0] <= cutoff:
            self.samples.popleft()
        ratio = integrated_ratio_last(list(self.samples), self.window_ms)
        self.points.append((float(timestamp), ratio))
        while self.points and self.points[0][0] < cutoff:
            self.points.popleft()
        return True

    def clear(self) -> None:
        self.samples.clear()
        self.points.clear()


def calculate_yield_statistics(
    area_hist: list[tuple[float, float, float]],
    bin_duration_sec: float = DEFAULT_BIN_SEC,
) -> dict[str, Any]:
    if len(area_hist) < 2:
        return {
            "net_average": 0.0,
            "total_half_area": 0.0,
            "total_piece_area": 0.0,
            "total_area": 0.0,
            "duration_sec": 0.0,
            "binned_data": [],
        }
    samples = list(area_hist)
    total_half = 0.0
    total_piece = 0.0
    for index in range(len(samples) - 1):
        t0, half0, piece0 = samples[index]
        t1 = samples[index + 1][0]
        dt_sec = (t1 - t0) / 1000.0
        total_half += half0 * dt_sec
        total_piece += piece0 * dt_sec
    total = total_half + total_piece
    net = total_half / total if total > 0 else 0.0
    duration_sec = (samples[-1][0] - samples[0][0]) / 1000.0
    binned: list[tuple[float, float, float, float]] = []
    if duration_sec > 0 and bin_duration_sec > 0:
        bin_ms = float(bin_duration_sec) * 1000.0
        start_time = samples[0][0]
        end_time = samples[-1][0]
        current = start_time
        while current < end_time:
            bin_end = min(current + bin_ms, end_time)
            bin_half = 0.0
            bin_piece = 0.0
            for index in range(len(samples) - 1):
                t0, half0, piece0 = samples[index]
                t1 = samples[index + 1][0]
                seg_start = max(t0, current)
                seg_end = min(t1, bin_end)
                if seg_end > seg_start:
                    dt_sec = (seg_end - seg_start) / 1000.0
                    bin_half += half0 * dt_sec
                    bin_piece += piece0 * dt_sec
            bin_total = bin_half + bin_piece
            bin_yield = bin_half / bin_total if bin_total > 0 else 0.0
            binned.append(((current - start_time) / 1000.0, bin_yield, bin_half, bin_piece))
            current = bin_end
    return {
        "net_average": net,
        "total_half_area": total_half,
        "total_piece_area": total_piece,
        "total_area": total,
        "duration_sec": duration_sec,
        "binned_data": binned,
    }


def sample_timestamps_ms(duration_ms: float, frames_per_sec: float) -> list[float]:
    """Video-time sample points for n scored frames each second."""
    if duration_ms < 0 or frames_per_sec <= 0:
        return []
    interval = 1000.0 / float(frames_per_sec)
    if not math.isfinite(interval) or interval <= 0:
        return []
    times: list[float] = []
    t = 0.0
    # Include the last frame when duration is an exact multiple.
    while t <= float(duration_ms) + 1e-6:
        times.append(round(t, 6))
        t += interval
    return times


def clamp_frames_per_sec(requested: float, video_fps: float) -> float:
    """Keep the sample rate at least 1/60 and at most the video's own fps."""
    try:
        value = float(requested)
    except (TypeError, ValueError):
        value = 1.0
    if not math.isfinite(value) or value <= 0:
        value = 1.0
    fps = float(video_fps) if video_fps and math.isfinite(float(video_fps)) else 1.0
    if fps <= 0:
        fps = 1.0
    return max(1.0 / 60.0, min(value, fps))


def save_yield_report(
    stats: dict[str, Any],
    output_path: Path,
    *,
    video_path: Path | None = None,
    model_filename: str | None = None,
    frames_per_sec: float | None = None,
) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    calibration = stats.get("calibration") or {}
    with output_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["# Half Yield Analysis Report"])
        writer.writerow(["# Generated:", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
        if video_path is not None:
            writer.writerow(["# Video:", str(video_path)])
        if model_filename:
            writer.writerow(["# Model:", str(model_filename)])
        if frames_per_sec is not None:
            writer.writerow(["# Frames per second:", f"{float(frames_per_sec):.4g}"])
        writer.writerow(["# Duration (seconds):", f"{float(stats.get('duration_sec') or 0):.2f}"])
        writer.writerow(["# Net Average Yield:", f"{float(stats.get('net_average') or 0):.6f}"])
        writer.writerow(["# Total Half Area:", f"{float(stats.get('total_half_area') or 0):.2f}"])
        writer.writerow(["# Total Piece Area:", f"{float(stats.get('total_piece_area') or 0):.2f}"])
        for name, value in calibration.items():
            writer.writerow([f"# Calibration {name}:", value])
        writer.writerow(["# Estimated Half Mass Share:", stats.get("estimated_half_mass_share")])
        writer.writerow(
            [
                "# Mass estimate basis:",
                "Area coefficients; not measured weight or unique material throughput",
            ]
        )
        writer.writerow([])
        writer.writerow(
            [
                "Time_Start_Seconds",
                "Yield_Ratio",
                "Half_Area_Integrated",
                "Piece_Area_Integrated",
                "Total_Area",
                "Estimated_Half_Mass_Share",
            ]
        )
        half_coef = float(calibration.get("half_mass_per_area_g_per_model_pixel2", 0.001))
        piece_coef = float(calibration.get("piece_mass_per_area_g_per_model_pixel2", 0.001))
        for bin_start, bin_yield, bin_half, bin_piece in stats.get("binned_data") or []:
            half_mass = float(bin_half) * half_coef
            piece_mass = float(bin_piece) * piece_coef
            mass_share = (
                half_mass / (half_mass + piece_mass) if half_mass + piece_mass > 0 else ""
            )
            writer.writerow(
                [
                    f"{float(bin_start):.1f}",
                    f"{float(bin_yield):.6f}",
                    f"{float(bin_half):.2f}",
                    f"{float(bin_piece):.2f}",
                    f"{float(bin_half) + float(bin_piece):.2f}",
                    mass_share,
                ]
            )
    return output_path


def _safe_column(name: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_]+", "_", str(name or "")).strip("_")
    return text or "value"


def save_crack_report(
    stats: dict[str, Any],
    output_path: Path,
    *,
    video_path: Path | None = None,
    model_filename: str | None = None,
    frames_per_sec: float | None = None,
) -> Path:
    """Write a review CSV for Crack Analyst / custom-metric models."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    calibration = stats.get("calibration") or {}
    class_names = [str(n) for n in (stats.get("class_names") or [])]
    metrics = list(stats.get("metrics") or [])
    pecan_cards = stats.get("pecan_builtins") or {}
    final_custom = stats.get("custom_values") or {}
    include_pecan = bool(stats.get("include_pecan"))
    with output_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["# Crack Analyst Review Report"])
        writer.writerow(["# Generated:", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
        if video_path is not None:
            writer.writerow(["# Video:", str(video_path)])
        if model_filename:
            writer.writerow(["# Model:", str(model_filename)])
        if frames_per_sec is not None:
            writer.writerow(["# Frames per second:", f"{float(frames_per_sec):.4g}"])
        writer.writerow(["# Duration (seconds):", f"{float(stats.get('duration_sec') or 0):.2f}"])
        writer.writerow(["# Frames scored:", int(stats.get("frames") or 0)])
        for name, value in calibration.items():
            if isinstance(value, dict):
                for key, nested in value.items():
                    writer.writerow([f"# Calibration {name}.{key}:", nested])
            else:
                writer.writerow([f"# Calibration {name}:", value])
        if include_pecan:
            for mid in (
                "pecan_exposed_kernel_mean",
                "pecan_exposed_kernel_median",
                "pecan_broken_kernel_area_per_100",
            ):
                card = pecan_cards.get(mid) or {}
                ready = card.get("ready")
                value = card.get("value")
                writer.writerow([f"# Final {mid}:", "" if value is None else value])
                writer.writerow(
                    [
                        f"# Final {mid} ready:",
                        "yes" if ready else f"no ({card.get('message') or 'insufficient data'})",
                    ]
                )
            n_card = next(
                (card for card in pecan_cards.values() if isinstance(card, dict) and "n" in card),
                {},
            )
            writer.writerow(["# Final pecan_n:", int(n_card.get("n") or 0)])
        for metric in metrics:
            mid = str(metric.get("id") or "")
            if not mid:
                continue
            label = str(metric.get("name") or mid)
            writer.writerow(
                [
                    f"# Final {label}:",
                    "" if final_custom.get(mid) is None else final_custom.get(mid),
                ]
            )
            if label != mid:
                writer.writerow([f"# Final {label} id:", mid])
        writer.writerow([])
        header = ["Time_Start_Seconds"]
        for name in class_names:
            safe = _safe_column(name)
            header.append(f"count_{safe}")
            header.append(f"area_{safe}")
        if include_pecan:
            header.extend(
                [
                    "pecan_exposed_kernel_mean",
                    "pecan_exposed_kernel_median",
                    "pecan_broken_kernel_area_per_100",
                    "pecan_n",
                ]
            )
        for metric in metrics:
            mid = _safe_column(metric.get("id") or "metric")
            header.append(f"{mid}_value")
        writer.writerow(header)
        for row in stats.get("binned_rows") or []:
            writer.writerow(row)
    return output_path


def clip_roi_to_frame(roi: dict[str, int], width: int, height: int) -> dict[str, int] | None:
    x1 = max(0, int(roi["x"]))
    y1 = max(0, int(roi["y"]))
    x2 = min(int(width), x1 + int(roi["w"]))
    y2 = min(int(height), y1 + int(roi["h"]))
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    return {"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1}


def scale_roi_to_frame(
    roi: dict[str, int],
    src_width: int,
    src_height: int,
    dst_width: int,
    dst_height: int,
) -> dict[str, int]:
    """Map an ROI from one frame size onto another (e.g. camera → infer JPEG)."""
    sw = int(src_width)
    sh = int(src_height)
    dw = int(dst_width)
    dh = int(dst_height)
    if sw <= 0 or sh <= 0 or dw <= 0 or dh <= 0:
        return {
            "x": int(roi["x"]),
            "y": int(roi["y"]),
            "w": int(roi["w"]),
            "h": int(roi["h"]),
        }
    if sw == dw and sh == dh:
        return {
            "x": int(roi["x"]),
            "y": int(roi["y"]),
            "w": int(roi["w"]),
            "h": int(roi["h"]),
        }
    sx = dw / float(sw)
    sy = dh / float(sh)
    return {
        "x": int(round(int(roi["x"]) * sx)),
        "y": int(round(int(roi["y"]) * sy)),
        "w": max(1, int(round(int(roi["w"]) * sx))),
        "h": max(1, int(round(int(roi["h"]) * sy))),
    }


def roi_fit_warning(roi: dict[str, int], width: int, height: int) -> str | None:
    needed_w = int(roi["x"]) + int(roi["w"])
    needed_h = int(roi["y"]) + int(roi["h"])
    if width >= needed_w and height >= needed_h:
        return None
    clipped = clip_roi_to_frame(roi, width, height)
    if clipped is None:
        return (
            f"This video is {width}×{height}. The measurement region "
            f"{roi['w']}×{roi['h']} at ({roi['x']}, {roi['y']}) does not fit."
        )
    return (
        f"This video is {width}×{height}. The default region needs at least "
        f"{needed_w}×{needed_h}; it will be clipped to "
        f"{clipped['w']}×{clipped['h']} at ({clipped['x']}, {clipped['y']})."
    )


def _clamp01(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if not math.isfinite(number):
        number = float(default)
    return min(1.0, max(0.0, number))


def _clamp_mass(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if not math.isfinite(number):
        number = float(default)
    return min(1.0, max(1e-6, number))
