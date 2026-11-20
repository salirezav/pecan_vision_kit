"""Offline video review: score a saved recording with the selected model."""

from __future__ import annotations

import base64
import math
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

import cv2

import calibration_store
import review_runs
from metric_formula import evaluate_formula
from pecan_kpis import (
    BUILTIN_IDS,
    HALFYIELD_BUILTIN_ID,
    empty_frame_samples,
    halfyield_builtin_metric,
    merge_run_samples,
    pecan_classes_ready,
    summarize_run,
)
from storage import list_videos, video_path_in_folder
from yield_measure import (
    DEFAULT_BIN_SEC,
    DEFAULT_ROI,
    DEFAULT_WINDOW_SEC,
    Calibration,
    YieldHistory,
    area_share,
    calculate_yield_statistics,
    clamp_frames_per_sec,
    clip_roi_to_frame,
    is_halfyield_classes,
    parse_roi,
    roi_fit_warning,
    save_crack_report,
    save_yield_report,
)

HISTORY = 90
SLOW_FACTOR = 3.0
SEEK_SEC = 5.0
ALLOWED_ACTIONS = frozenset(
    {
        "toggle_pause",
        "toggle_slow",
        "seek_back",
        "seek_forward",
        "prev_frame",
        "next_frame",
        "finish",
    }
)


def frame_to_jpeg(frame: Any, *, quality: int = 85) -> bytes:
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise RuntimeError("Could not encode the video frame.")
    return buf.tobytes()


def jpeg_data_url(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")


def default_review_roi(width: int, height: int) -> dict[str, int] | None:
    """Prefer the half-yield box when it fits; otherwise measure the full frame."""
    needed_w = int(DEFAULT_ROI["x"]) + int(DEFAULT_ROI["w"])
    needed_h = int(DEFAULT_ROI["y"]) + int(DEFAULT_ROI["h"])
    if width >= needed_w and height >= needed_h:
        return dict(DEFAULT_ROI)
    return None


def video_fps(cap: cv2.VideoCapture, reported: float | None = None) -> float:
    """Stable fps for timing; many AVIs report 0 from OpenCV."""
    try:
        value = float(reported if reported is not None else cap.get(cv2.CAP_PROP_FPS) or 0)
    except (TypeError, ValueError):
        value = 0.0
    if math.isfinite(value) and value > 1e-3:
        return value
    return 30.0


def frame_time_ms(frame_index: int, fps: float) -> float:
    rate = float(fps) if fps and fps > 1e-3 else 30.0
    return max(0.0, float(frame_index) / rate * 1000.0)


def video_duration_ms(frame_count: int, fps: float) -> float:
    if frame_count <= 0:
        return 0.0
    rate = float(fps) if fps and fps > 1e-3 else 30.0
    # Last frame index is frame_count - 1; duration spans through that frame.
    return frame_time_ms(max(0, int(frame_count) - 1), rate)


class ReviewVideoService:
    def __init__(
        self,
        *,
        predict: Callable[..., dict[str, Any]],
        model_status: Callable[[], dict[str, Any]],
        get_calibration: Callable[[], dict[str, Any]],
        list_metrics: Callable[[], list[dict[str, Any]]],
        builtin_enabled: Callable[[], dict[str, bool]],
        videos_dir: Callable[[], Path],
        model_busy: type[Exception],
        deploy_running: Callable[[], bool] | None = None,
    ) -> None:
        self._predict = predict
        self._model_status = model_status
        self._get_calibration = get_calibration
        self._list_metrics = list_metrics
        self._builtin_enabled = builtin_enabled
        self._videos_dir = videos_dir
        self._model_busy = model_busy
        self._deploy_running = deploy_running or (lambda: False)
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._thread: threading.Thread | None = None
        self._generation = 0
        self._running = False
        self._paused = False
        self._slow = False
        self._status = "ready"
        self._error: str | None = None
        self._actions: deque[str] = deque()
        self._clear_segment = False
        self._finish_requested = False

        self._video_name = ""
        self._video_path: Path | None = None
        self._frames_per_sec = 1.0
        self._window_sec = DEFAULT_WINDOW_SEC
        self._roi: dict[str, int] | None = None
        self._roi_warning: str | None = None
        self._halfyield = False
        self._class_names: list[str] = []
        self._model_filename = ""
        self._calibration: dict[str, Any] = {}
        self._run_metrics: list[dict[str, Any]] = []
        self._builtins_on: dict[str, bool] = {}
        self._include_pecan = False

        self._fps = 0.0
        self._frame_count = 0
        self._width = 0
        self._height = 0
        self._duration_ms = 0.0
        self._position_ms = 0.0
        self._frame_index = 0
        self._run_frames = 0
        self._run_id: str | None = None
        self._last_image: str | None = None
        self._last_elapsed_ms: int | None = None
        self._still_image: str | None = None

        self._yield_samples: list[tuple[float, float, float]] = []
        self._yield_history: YieldHistory | None = None
        self._mass_share_run: float | None = None
        self._mass_share_frame: float | None = None
        self._area_share_frame: float | None = None
        self._half_coef = calibration_store.DEFAULT_MASS_PER_AREA
        self._piece_coef = calibration_store.DEFAULT_MASS_PER_AREA
        self._trend: list[dict[str, float]] = []

        self._pecan_run = empty_frame_samples()
        self._pecan_builtins: dict[str, dict[str, Any]] = {}
        self._run_sums: dict[str, dict[str, float]] = {}
        self._custom_values: dict[str, float | None] = {}
        self._crack_timeline: list[dict[str, Any]] = []
        self._final_kpis: dict[str, Any] | None = None
        self._report_path: str | None = None

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def status(self) -> dict[str, Any]:
        with self._lock:
            half_share = self._area_share_frame
            piece_share = None if half_share is None else 1.0 - float(half_share)
            return {
                "status": self._status,
                "running": self._running,
                "paused": self._paused,
                "slow": self._slow,
                "error": self._error,
                "video_name": self._video_name or None,
                "frames_per_sec": self._frames_per_sec,
                "window_sec": self._window_sec,
                "roi": dict(self._roi) if self._roi else None,
                "full_frame": self._roi is None,
                "roi_warning": self._roi_warning,
                "halfyield": self._halfyield,
                "class_names": list(self._class_names),
                "model_filename": self._model_filename or None,
                "calibration": dict(self._calibration) if self._calibration else None,
                "fps": self._fps,
                "frame_count": self._frame_count,
                "width": self._width,
                "height": self._height,
                "duration_ms": self._duration_ms,
                "position_ms": self._position_ms,
                "frame_index": self._frame_index,
                "frames": self._run_frames,
                "elapsed_ms": self._last_elapsed_ms,
                "image": self._last_image,
                "still_image": self._still_image,
                "run_id": self._run_id,
                "report": self._report_path,
                "half_share": half_share,
                "piece_share": piece_share,
                "mass_share": self._mass_share_run,
                "mass_share_frame": self._mass_share_frame,
                "trend": list(self._trend),
                "pecan_builtins": {
                    key: dict(val) for key, val in self._pecan_builtins.items()
                },
                "run_sums": {key: dict(val) for key, val in self._run_sums.items()},
                "custom_values": dict(self._custom_values),
                "metrics": list(self._run_metrics),
                "final_kpis": dict(self._final_kpis) if self._final_kpis else None,
                "segment_note": (
                    "Seeking, stepping, or changing calibration starts a new report segment."
                ),
            }

    def still(self, video_name: str) -> dict[str, Any]:
        path = video_path_in_folder(self._videos_dir(), video_name)
        if not path.is_file():
            raise FileNotFoundError("That video was not found.")
        cap = cv2.VideoCapture(str(path))
        try:
            if not cap.isOpened():
                raise RuntimeError("Could not open that video.")
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            ok, frame = cap.read()
            if not ok or frame is None:
                raise RuntimeError("Could not read a frame from that video.")
            if height <= 0 or width <= 0:
                height, width = frame.shape[:2]
            jpeg = frame_to_jpeg(frame, quality=80)
            roi = default_review_roi(width, height)
            warning = roi_fit_warning(DEFAULT_ROI, width, height) if roi else None
            with self._lock:
                self._still_image = jpeg_data_url(jpeg)
                self._video_name = path.name
                self._width = width
                self._height = height
                self._fps = fps
                self._frame_count = frame_count
                self._roi_warning = warning
            return {
                "ok": True,
                "video_name": path.name,
                "width": width,
                "height": height,
                "fps": fps,
                "frame_count": frame_count,
                "max_frames_per_sec": max(fps, 1.0 / 60.0) if fps > 0 else 1.0,
                "image": jpeg_data_url(jpeg),
                "default_roi": roi,
                "roi_warning": warning,
            }
        finally:
            cap.release()

    def start(
        self,
        *,
        video_name: str,
        frames_per_sec: float = 1.0,
        window_sec: float = DEFAULT_WINDOW_SEC,
        roi: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._deploy_running():
            raise RuntimeError("Stop Deploy before starting a video review.")
        model = self._model_status() or {}
        if not model.get("installed"):
            raise RuntimeError("Upload or select a model first.")
        if not model.get("library_ready"):
            raise RuntimeError(
                model.get("library_error") or "The model library is not ready."
            )
        path = video_path_in_folder(self._videos_dir(), video_name)
        if not path.is_file():
            raise FileNotFoundError("That video was not found.")

        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            cap.release()
            raise RuntimeError("Could not open that video.")
        reported_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        fps = video_fps(cap, reported_fps)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        ok, first = cap.read()
        if not ok or first is None:
            cap.release()
            raise RuntimeError("Could not read a frame from that video.")
        if width <= 0 or height <= 0:
            height, width = first.shape[:2]
        duration_ms = video_duration_ms(frame_count, fps)
        sample_rate = clamp_frames_per_sec(frames_per_sec, fps)
        try:
            window = float(window_sec)
        except (TypeError, ValueError):
            window = DEFAULT_WINDOW_SEC
        if not math.isfinite(window) or window <= 0 or window > 86400:
            raise ValueError("Choose a positive measurement window up to 86400 seconds.")

        # null / false / {} means the whole frame. The still endpoint may suggest
        # a default box in the UI; clearing it must keep full-frame scoring.
        parsed_roi: dict[str, int] | None
        if roi is None or roi is False or roi == {}:
            parsed_roi = None
        else:
            parsed_roi = parse_roi(roi)
            clipped = clip_roi_to_frame(parsed_roi, width, height)
            if clipped is None:
                raise ValueError("The measurement region does not fit this video.")
            parsed_roi = clipped
        warning = None
        if parsed_roi is not None:
            warning = roi_fit_warning(parsed_roi, width, height)

        class_names = [str(n) for n in (model.get("class_names") or [])]
        halfyield = bool(model.get("halfyield")) or is_halfyield_classes(class_names)
        try:
            calibration = dict(self._get_calibration() or {})
        except Exception:  # noqa: BLE001
            calibration = calibration_store.load(
                str(model.get("filename") or ""), class_names
            )
        half_coef, piece_coef = calibration_store.mass_coefficients(calibration)
        builtins_on = dict(self._builtin_enabled() or {})
        if halfyield:
            metrics = (
                [halfyield_builtin_metric(enabled=True)]
                if builtins_on.get(HALFYIELD_BUILTIN_ID, True) is not False
                else []
            )
            include_pecan = False
        else:
            metrics = list(self._list_metrics())
            include_pecan = pecan_classes_ready(class_names) and any(
                builtins_on.get(mid, True) is not False for mid in BUILTIN_IDS
            )

        with self._condition:
            if self._running:
                cap.release()
                raise RuntimeError("A review is already running.")
            self._generation += 1
            generation = self._generation
            self._running = True
            self._paused = False
            self._slow = False
            self._status = "loading"
            self._error = None
            self._actions.clear()
            self._clear_segment = False
            self._finish_requested = False
            self._video_name = path.name
            self._video_path = path
            self._frames_per_sec = sample_rate
            self._window_sec = window
            self._roi = dict(parsed_roi) if parsed_roi else None
            self._roi_warning = warning
            self._halfyield = halfyield
            self._class_names = class_names
            self._model_filename = str(model.get("filename") or "")
            self._calibration = calibration
            self._run_metrics = metrics
            self._builtins_on = builtins_on
            self._include_pecan = include_pecan
            self._fps = fps
            self._frame_count = frame_count
            self._width = width
            self._height = height
            self._duration_ms = duration_ms
            self._position_ms = 0.0
            self._frame_index = 0
            self._run_frames = 0
            self._last_image = None
            self._last_elapsed_ms = None
            self._still_image = jpeg_data_url(frame_to_jpeg(first, quality=80))
            self._half_coef = half_coef
            self._piece_coef = piece_coef
            self._final_kpis = None
            self._report_path = None
            self._reset_segment_locked()
            summary = review_runs.open_run(
                video_name=path.name,
                video_path=str(path),
                model_filename=self._model_filename or None,
                halfyield=halfyield,
                frames_per_sec=sample_rate,
                window_sec=window,
                roi=self._roi,
                class_names=class_names,
            )
            self._run_id = str(summary.get("id") or "")
            self._thread = threading.Thread(
                target=self._loop,
                args=(generation, cap),
                name="review-video",
                daemon=True,
            )
            self._thread.start()
            return self.status()

    def action(self, name: str) -> dict[str, Any]:
        key = str(name or "").strip()
        if key not in ALLOWED_ACTIONS:
            raise ValueError("Unknown review action.")
        with self._condition:
            if not self._running and key != "finish":
                raise RuntimeError("Start a review first.")
            if key == "finish":
                self._finish_requested = True
                self._actions.append("finish")
            else:
                self._actions.append(key)
            self._condition.notify_all()
        if key == "finish":
            return self._wait_until_stopped()
        return self.status()

    def finish(self) -> dict[str, Any]:
        return self.action("finish")

    def _wait_until_stopped(self) -> dict[str, Any]:
        thread = None
        with self._lock:
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=120.0)
        return self.status()

    def refresh_calibration(self) -> dict[str, Any]:
        with self._condition:
            if not self._running:
                return self.status()
            try:
                self._calibration = dict(self._get_calibration() or {})
            except Exception:  # noqa: BLE001
                self._calibration = calibration_store.load(
                    self._model_filename, self._class_names
                )
            half_coef, piece_coef = calibration_store.mass_coefficients(self._calibration)
            self._half_coef = half_coef
            self._piece_coef = piece_coef
            self._clear_segment = True
            self._condition.notify_all()
            return self.status()

    def _reset_segment_locked(self) -> None:
        self._yield_samples = []
        self._yield_history = YieldHistory(self._window_sec * 1000.0)
        self._mass_share_run = None
        self._mass_share_frame = None
        self._area_share_frame = None
        self._trend = []
        self._pecan_run = empty_frame_samples()
        self._pecan_builtins = {}
        self._run_sums = {}
        self._custom_values = {}
        self._crack_timeline = []
        self._run_frames = 0

    def _take_action_locked(self) -> str | None:
        if self._actions:
            return self._actions.popleft()
        return None

    def _wait_for_action_or_timeout(self, timeout_sec: float) -> str | None:
        deadline = time.monotonic() + max(0.0, timeout_sec)
        with self._condition:
            while True:
                if self._clear_segment:
                    self._clear_segment = False
                    self._reset_segment_locked()
                action = self._take_action_locked()
                if action:
                    return action
                if not self._running:
                    return "finish"
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(timeout=min(0.1, remaining))

    def _mark_end_locked(self) -> None:
        """Pin the clock to the end so the progress bar fills before finalize."""
        if self._duration_ms <= 0 and self._position_ms > 0:
            self._duration_ms = self._position_ms
        if self._duration_ms > 0:
            self._position_ms = self._duration_ms
        if self._frame_count > 0:
            self._frame_index = max(0, self._frame_count - 1)

    def _loop(self, generation: int, cap: cv2.VideoCapture) -> None:
        try:
            with self._lock:
                if generation != self._generation:
                    return
                self._status = "reviewing"
                fps = video_fps(cap, self._fps)
                self._fps = fps
                sample_interval_ms = 1000.0 / self._frames_per_sec
                roi = dict(self._roi) if self._roi else None
                width = self._width
                height = self._height
                frame_count = self._frame_count
                if self._duration_ms <= 0 and frame_count > 0:
                    self._duration_ms = video_duration_ms(frame_count, fps)

            # Rewind to the first frame after the peek in start().
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            next_sample_index = 0
            sample_step = max(1, int(round(fps / self._frames_per_sec)))

            while True:
                with self._lock:
                    if generation != self._generation or not self._running:
                        break
                    if self._finish_requested:
                        break
                    paused = self._paused
                    slow = self._slow
                    if self._clear_segment:
                        self._clear_segment = False
                        self._reset_segment_locked()

                if paused:
                    action = self._wait_for_action_or_timeout(0.2)
                    if action:
                        if self._handle_action(action, cap, fps, frame_count):
                            break
                        with self._lock:
                            next_sample_index = int(self._frame_index)
                    continue

                if frame_count > 0 and next_sample_index >= frame_count:
                    with self._lock:
                        self._mark_end_locked()
                    break

                target_index = max(0, int(next_sample_index))
                if frame_count > 0:
                    target_index = min(frame_count - 1, target_index)
                current_index = int(cap.get(cv2.CAP_PROP_POS_FRAMES) or 0)
                if abs(current_index - target_index) > 1:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, target_index)
                ok, frame = cap.read()
                if not ok or frame is None:
                    # End of video — save the report automatically.
                    with self._lock:
                        self._mark_end_locked()
                    break

                frame_index = target_index
                position_ms = frame_time_ms(frame_index, fps)
                started = time.monotonic()
                try:
                    result = self._score_frame(frame, roi, width, height)
                except self._model_busy:
                    time.sleep(0.05)
                    continue
                except Exception as exc:  # noqa: BLE001
                    with self._lock:
                        if generation == self._generation:
                            self._error = str(exc)
                    time.sleep(0.5)
                    continue

                self._ingest_result(result, position_ms, frame_index, generation)
                with self._lock:
                    if self._duration_ms <= 0:
                        self._duration_ms = max(self._duration_ms, position_ms)
                    elif position_ms > self._duration_ms:
                        self._duration_ms = position_ms

                next_sample_index = frame_index + sample_step
                if frame_count > 0 and next_sample_index >= frame_count:
                    with self._lock:
                        self._mark_end_locked()
                    break

                delay = sample_interval_ms / 1000.0
                if slow:
                    delay *= SLOW_FACTOR
                delay -= time.monotonic() - started
                action = self._wait_for_action_or_timeout(delay)
                if action:
                    if self._handle_action(action, cap, fps, frame_count):
                        break
                    with self._lock:
                        next_sample_index = int(self._frame_index)
        finally:
            try:
                cap.release()
            except Exception:  # noqa: BLE001
                pass
            self._finalize_locked(generation)

    def _handle_action(
        self,
        action: str,
        cap: cv2.VideoCapture,
        fps: float,
        frame_count: int,
    ) -> bool:
        """Apply a transport action. Returns True when the loop should exit."""
        if action == "finish":
            return True
        with self._lock:
            if action == "toggle_pause":
                self._paused = not self._paused
                return False
            if action == "toggle_slow":
                self._slow = not self._slow
                return False

            # Seek / step clears the report segment.
            self._reset_segment_locked()
            position_ms = self._position_ms
            frame_index = self._frame_index

        rate = video_fps(cap, fps)
        if action == "seek_back":
            position_ms = max(0.0, position_ms - SEEK_SEC * 1000.0)
        elif action == "seek_forward":
            limit = video_duration_ms(frame_count, rate) if frame_count else position_ms
            position_ms = min(limit, position_ms + SEEK_SEC * 1000.0)
        elif action == "prev_frame":
            frame_index = max(0, frame_index - 1)
            position_ms = frame_time_ms(frame_index, rate)
        elif action == "next_frame":
            if frame_count > 0:
                frame_index = min(frame_count - 1, frame_index + 1)
            else:
                frame_index += 1
            position_ms = frame_time_ms(frame_index, rate)

        if action in {"seek_back", "seek_forward"}:
            frame_index = int(round((position_ms / 1000.0) * rate))
            if frame_count > 0:
                frame_index = max(0, min(frame_count - 1, frame_index))
            position_ms = frame_time_ms(frame_index, rate)

        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_index))
        ok, frame = cap.read()
        if ok and frame is not None:
            with self._lock:
                roi = dict(self._roi) if self._roi else None
                width = self._width
                height = self._height
            try:
                result = self._score_frame(frame, roi, width, height)
                # Inspection frames are shown but do not join the segment until
                # continuous playback appends the next sample after a resume.
                image = result.get("image")
                with self._lock:
                    self._position_ms = position_ms
                    self._frame_index = frame_index
                    self._last_elapsed_ms = int(result.get("elapsed_ms") or 0)
                    if image:
                        self._last_image = image
                    self._paused = True
                    if self._halfyield:
                        half_area = float(result.get("half_area") or 0)
                        piece_area = float(result.get("piece_area") or 0)
                        share = area_share(half_area, piece_area)
                        self._area_share_frame = share
                        cal = Calibration(
                            half_mass_per_area=self._half_coef,
                            piece_mass_per_area=self._piece_coef,
                        )
                        self._mass_share_frame = cal.mass_share(share)
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    self._error = str(exc)
                    self._paused = True
        else:
            with self._lock:
                self._position_ms = position_ms
                self._frame_index = frame_index
                self._paused = True
        return False

    def _score_frame(
        self,
        frame: Any,
        roi: dict[str, int] | None,
        width: int,
        height: int,
    ) -> dict[str, Any]:
        jpeg = frame_to_jpeg(frame, quality=85)
        try:
            calibration = self._get_calibration()
        except Exception:  # noqa: BLE001
            calibration = None
        with self._lock:
            halfyield = self._halfyield
            if calibration:
                self._calibration = dict(calibration)
                half_coef, piece_coef = calibration_store.mass_coefficients(calibration)
                self._half_coef = half_coef
                self._piece_coef = piece_coef
        return self._predict(
            jpeg,
            0.5,
            "accurate" if halfyield else "accurate",
            draw=True,
            roi=roi,
            roi_space={"width": width, "height": height} if width and height else None,
            calibration=calibration,
        )

    def _ingest_result(
        self,
        result: dict[str, Any],
        position_ms: float,
        frame_index: int,
        generation: int,
    ) -> None:
        with self._lock:
            if generation != self._generation or not self._running:
                return
            image = result.get("image")
            if image:
                self._last_image = image
            self._last_elapsed_ms = int(result.get("elapsed_ms") or 0)
            self._position_ms = float(position_ms)
            self._frame_index = int(frame_index)
            self._error = None
            self._run_frames += 1

            measurements = result.get("measurements") or []
            frame_map: dict[str, dict[str, float]] = {}
            for row in measurements:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("class") or "")
                if not name:
                    continue
                count = float(row.get("count") or 0)
                area = float(row.get("area") or 0)
                frame_map[name] = {"count": count, "area": area}
                cell = self._run_sums.setdefault(name, {"count": 0.0, "area": 0.0})
                cell["count"] += count
                cell["area"] += area

            if self._halfyield or result.get("halfyield"):
                half_area = float(result.get("half_area") or 0)
                piece_area = float(result.get("piece_area") or 0)
                if self._yield_samples and position_ms <= self._yield_samples[-1][0]:
                    position_ms = self._yield_samples[-1][0] + 1.0
                self._yield_samples.append((position_ms, half_area, piece_area))
                if self._yield_history is None:
                    self._yield_history = YieldHistory(self._window_sec * 1000.0)
                self._yield_history.append(position_ms, half_area, piece_area)
                share = area_share(half_area, piece_area)
                self._area_share_frame = share
                cal = Calibration(
                    half_mass_per_area=self._half_coef,
                    piece_mass_per_area=self._piece_coef,
                )
                self._mass_share_frame = cal.mass_share(share)
                stats = calculate_yield_statistics(self._yield_samples, DEFAULT_BIN_SEC)
                run_share = stats.get("net_average")
                self._mass_share_run = cal.mass_share(
                    float(run_share) if run_share is not None else None
                )
                # Prefer rolling-window ratio for the live reading when available.
                rolling = self._yield_history.current
                if rolling is not None:
                    self._area_share_frame = rolling
                    self._mass_share_frame = cal.mass_share(rolling)
                self._trend = [
                    {"t": float(ts), "value": float(val)}
                    for ts, val in list(self._yield_history.points)[-HISTORY:]
                    if val is not None
                ]
                self._final_kpis = {
                    "net_average": stats.get("net_average"),
                    "estimated_half_mass_share": self._mass_share_run,
                    "duration_sec": stats.get("duration_sec"),
                    "total_half_area": stats.get("total_half_area"),
                    "total_piece_area": stats.get("total_piece_area"),
                }
            else:
                if self._include_pecan:
                    frame_samples = result.get("pecan_samples")
                    if isinstance(frame_samples, dict):
                        merge_run_samples(self._pecan_run, frame_samples)
                    summarized = summarize_run(self._pecan_run)
                    enabled = {
                        mid
                        for mid in BUILTIN_IDS
                        if self._builtins_on.get(mid, True) is not False
                    }
                    self._pecan_builtins = {
                        mid: dict(card)
                        for mid, card in summarized.items()
                        if mid in enabled
                    }
                custom: dict[str, float | None] = {}
                for metric in self._run_metrics:
                    mid = str(metric.get("id") or "")
                    formula = str(metric.get("formula") or "")
                    if not mid or not formula:
                        continue
                    try:
                        custom[mid] = evaluate_formula(formula, self._run_sums)
                    except Exception:  # noqa: BLE001
                        custom[mid] = None
                self._custom_values = custom
                self._crack_timeline.append(
                    {
                        "t": float(position_ms),
                        "frame_map": frame_map,
                        "pecan": {
                            mid: dict(card) for mid, card in self._pecan_builtins.items()
                        },
                        "custom": dict(custom),
                        "run_sums": {
                            key: dict(val) for key, val in self._run_sums.items()
                        },
                    }
                )
                self._final_kpis = self._crack_final_locked()

    def _crack_final_locked(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "custom_values": dict(self._custom_values),
            "frames": self._run_frames,
        }
        if self._include_pecan:
            payload["pecan_builtins"] = {
                key: dict(val) for key, val in self._pecan_builtins.items()
            }
            n = 0
            for card in self._pecan_builtins.values():
                if isinstance(card, dict) and card.get("n") is not None:
                    n = int(card.get("n") or 0)
                    break
            payload["pecan_n"] = n
        return payload

    def _calibration_metadata_locked(self) -> dict[str, Any]:
        cal = dict(self._calibration or {})
        if self._halfyield:
            conf = cal.get("class_confidence") or {}
            meta: dict[str, Any] = {}
            for bucket in ("half", "piece", "obscured", "shell"):
                values = [
                    float(conf[name])
                    for name in self._class_names
                    if bucket in str(name).lower() and name in conf
                ]
                if values:
                    meta[f"{bucket}_confidence"] = min(values)
                elif f"{bucket}_confidence" in cal:
                    meta[f"{bucket}_confidence"] = cal[f"{bucket}_confidence"]
            meta["half_mass_per_area_g_per_model_pixel2"] = self._half_coef
            meta["piece_mass_per_area_g_per_model_pixel2"] = self._piece_coef
            return meta
        return cal

    def _build_crack_bins_locked(self) -> list[list[Any]]:
        timeline = list(self._crack_timeline)
        if len(timeline) < 1:
            return []
        start = float(timeline[0]["t"])
        end = float(timeline[-1]["t"])
        duration_sec = max(0.0, (end - start) / 1000.0)
        bin_ms = DEFAULT_BIN_SEC * 1000.0
        rows: list[list[Any]] = []
        current = start
        # Snapshot at the last sample that falls inside each bin.
        while current <= end + 1e-6:
            bin_end = min(current + bin_ms, end if end > current else current)
            snapshot = None
            for sample in timeline:
                if float(sample["t"]) + 1e-6 < current:
                    continue
                if float(sample["t"]) > bin_end + 1e-6:
                    break
                snapshot = sample
            if snapshot is None:
                # Carry forward the latest sample at or before bin start.
                for sample in timeline:
                    if float(sample["t"]) <= current + 1e-6:
                        snapshot = sample
                    else:
                        break
            if snapshot is None:
                current = bin_end if bin_end > current else current + bin_ms
                if current == bin_end and bin_end >= end:
                    break
                continue
            row: list[Any] = [f"{(current - start) / 1000.0:.1f}"]
            frame_map = snapshot.get("run_sums") or {}
            for name in self._class_names:
                cell = frame_map.get(name) or {}
                row.append(float(cell.get("count") or 0))
                row.append(float(cell.get("area") or 0))
            if self._include_pecan:
                pecan = snapshot.get("pecan") or {}
                for mid in (
                    "pecan_exposed_kernel_mean",
                    "pecan_exposed_kernel_median",
                    "pecan_broken_kernel_area_per_100",
                ):
                    card = pecan.get(mid) or {}
                    value = card.get("value")
                    row.append("" if value is None else float(value))
                n = 0
                for card in pecan.values():
                    if isinstance(card, dict) and card.get("n") is not None:
                        n = int(card.get("n") or 0)
                        break
                row.append(n)
            custom = snapshot.get("custom") or {}
            for metric in self._run_metrics:
                mid = str(metric.get("id") or "")
                value = custom.get(mid)
                row.append("" if value is None else float(value))
            rows.append(row)
            if bin_end >= end:
                break
            current = bin_end
        if not rows and timeline:
            # Single-sample segment: one bin at t=0 with cumulative totals.
            snapshot = timeline[-1]
            row = ["0.0"]
            frame_map = snapshot.get("run_sums") or {}
            for name in self._class_names:
                cell = frame_map.get(name) or {}
                row.append(float(cell.get("count") or 0))
                row.append(float(cell.get("area") or 0))
            if self._include_pecan:
                pecan = snapshot.get("pecan") or {}
                for mid in (
                    "pecan_exposed_kernel_mean",
                    "pecan_exposed_kernel_median",
                    "pecan_broken_kernel_area_per_100",
                ):
                    card = pecan.get(mid) or {}
                    value = card.get("value")
                    row.append("" if value is None else float(value))
                n = 0
                for card in pecan.values():
                    if isinstance(card, dict) and card.get("n") is not None:
                        n = int(card.get("n") or 0)
                        break
                row.append(n)
            custom = snapshot.get("custom") or {}
            for metric in self._run_metrics:
                mid = str(metric.get("id") or "")
                value = custom.get(mid)
                row.append("" if value is None else float(value))
            rows.append(row)
        del duration_sec
        return rows

    def _write_report_locked(self) -> Path | None:
        run_id = self._run_id
        if not run_id:
            return None
        folder = review_runs.run_dir(run_id)
        path = folder / "report.csv"
        video_path = self._video_path
        calibration = self._calibration_metadata_locked()
        if self._halfyield:
            stats = calculate_yield_statistics(self._yield_samples, DEFAULT_BIN_SEC)
            cal = Calibration(
                half_mass_per_area=self._half_coef,
                piece_mass_per_area=self._piece_coef,
            )
            mass = cal.mass_share(
                float(stats["net_average"]) if stats.get("net_average") is not None else None
            )
            stats["calibration"] = calibration
            stats["estimated_half_mass_share"] = mass
            self._final_kpis = {
                "net_average": stats.get("net_average"),
                "estimated_half_mass_share": mass,
                "duration_sec": stats.get("duration_sec"),
                "total_half_area": stats.get("total_half_area"),
                "total_piece_area": stats.get("total_piece_area"),
            }
            save_yield_report(
                stats,
                path,
                video_path=video_path,
                model_filename=self._model_filename or None,
                frames_per_sec=self._frames_per_sec,
            )
        else:
            duration_sec = 0.0
            if self._crack_timeline:
                duration_sec = (
                    float(self._crack_timeline[-1]["t"])
                    - float(self._crack_timeline[0]["t"])
                ) / 1000.0
            stats = {
                "calibration": calibration,
                "class_names": list(self._class_names),
                "metrics": list(self._run_metrics),
                "pecan_builtins": {
                    key: dict(val) for key, val in self._pecan_builtins.items()
                },
                "custom_values": dict(self._custom_values),
                "include_pecan": self._include_pecan,
                "duration_sec": duration_sec,
                "frames": self._run_frames,
                "binned_rows": self._build_crack_bins_locked(),
            }
            self._final_kpis = self._crack_final_locked()
            self._final_kpis["duration_sec"] = duration_sec
            save_crack_report(
                stats,
                path,
                video_path=video_path,
                model_filename=self._model_filename or None,
                frames_per_sec=self._frames_per_sec,
            )
        return path

    def _finalize_locked(self, generation: int) -> None:
        with self._lock:
            if generation != self._generation:
                return
            self._mark_end_locked()
            path = None
            try:
                path = self._write_report_locked()
            except Exception as exc:  # noqa: BLE001
                self._error = str(exc)
            self._running = False
            self._status = "finished" if not self._error else "error"
            self._paused = False
            self._report_path = str(path) if path else None
            run_id = self._run_id
            frames = self._run_frames
            final = dict(self._final_kpis) if self._final_kpis else None
            finished_ok = bool(path) and not self._error
        if run_id:
            try:
                review_runs.finalize_run(
                    run_id,
                    frames=frames,
                    status="finished" if finished_ok else "error",
                    final_kpis=final,
                )
            except Exception:  # noqa: BLE001
                pass


def list_review_videos(folder: Path) -> list[dict[str, Any]]:
    """Ready videos only (skip in-progress conversions and still images)."""
    rows = []
    for row in list_videos(folder):
        if row.get("status") != "ready":
            continue
        if row.get("kind") == "image":
            continue
        rows.append(row)
    return rows
