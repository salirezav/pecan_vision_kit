"""Server-owned half-yield video review that survives page navigation."""

from __future__ import annotations

import base64
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import cv2

from storage import is_allowed_path
from yield_measure import (
    DEFAULT_BIN_SEC,
    DEFAULT_ROI,
    DEFAULT_WINDOW_SEC,
    MIN_AREA_PX,
    SKIP_SECONDS,
    Calibration,
    YieldHistory,
    calculate_yield_statistics,
    parse_calibration,
    parse_roi,
    roi_fit_warning,
    save_yield_report,
)

ACTIONS = frozenset(
    {
        "toggle_pause",
        "toggle_slow",
        "seek_back",
        "seek_forward",
        "prev_frame",
        "next_frame",
        "left",
        "right",
        "quit",
    }
)


class ReviewLiveService:
    def __init__(
        self,
        *,
        predict_roi: Callable[..., dict[str, Any]],
        halfyield_status: Callable[[], dict[str, Any]],
        model_busy: type[Exception],
        list_videos: Callable[[], list[dict[str, Any]]],
    ) -> None:
        self._predict_roi = predict_roi
        self._halfyield_status = halfyield_status
        self._model_busy = model_busy
        self._list_videos = list_videos
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._generation = 0
        self._running = False
        self._status = "ready"
        self._error: str | None = None
        self._notice = (
            "CSV report is saved when you finish. Measurements use detected area, not weight."
        )
        self._source: Path | None = None
        self._report: Path | None = None
        self._window_seconds = DEFAULT_WINDOW_SEC
        self._analyze_interval_sec = 0.0
        self._roi = dict(DEFAULT_ROI)
        self._calibration = Calibration()
        self._paused = False
        self._slow = False
        self._complete = False
        self._position = 0.0
        self._duration = 0.0
        self._fps = 30.0
        self._frame_count = 0
        self._width = 0
        self._height = 0
        self._ratio: float | None = None
        self._mass_share: float | None = None
        self._series: list[list[float | None]] = []
        self._image: str | None = None
        self._elapsed_ms: int | None = None
        self._pending_calibration: Calibration | None = None
        self._actions: list[str] = []
        self._history = YieldHistory(DEFAULT_WINDOW_SEC * 1000.0)
        self._complete_hist: list[tuple[float, float, float]] = []
        self._fit_warning: str | None = None
        self._last_seen = time.monotonic()
        self._connected = False

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def status(self, *, heartbeat: bool = False) -> dict[str, Any]:
        with self._lock:
            if heartbeat:
                self._connected = True
                self._last_seen = time.monotonic()
            return {
                "status": self._status,
                "running": self._running,
                "error": self._error,
                "notice": self._notice,
                "source": self._source.name if self._source else None,
                "report": str(self._report) if self._report else None,
                "window_seconds": self._window_seconds,
                "analyze_interval_sec": self._analyze_interval_sec,
                "skip_seconds": SKIP_SECONDS,
                "roi": dict(self._roi),
                "default_roi": dict(DEFAULT_ROI),
                "calibration": self._calibration.metadata(),
                "paused": self._paused,
                "slow": self._slow,
                "complete": self._complete,
                "position": self._position,
                "duration": self._duration,
                "fps": self._fps,
                "frame_count": self._frame_count,
                "width": self._width,
                "height": self._height,
                "ratio": self._ratio,
                "mass_share": self._mass_share,
                "series": [list(point) for point in self._series],
                "image": self._image,
                "elapsed_ms": self._elapsed_ms,
                "fit_warning": self._fit_warning,
                "halfyield": self._halfyield_status(),
                "videos": self._list_videos(),
            }

    def start(self, values: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self._running:
                raise RuntimeError("A review is already running.")
            model = self._halfyield_status()
            if not model.get("installed"):
                raise RuntimeError(model.get("error") or "Place the half-yield model first.")
            if model.get("error"):
                raise RuntimeError(str(model["error"]))
            source = self._resolve_source(str(values.get("source") or ""))
            report = self._resolve_report(str(values.get("report") or ""), source)
            window = float(values.get("window") or DEFAULT_WINDOW_SEC)
            if not 0 < window <= 86400:
                raise ValueError("Choose a positive measurement window up to 86400 seconds.")
            try:
                analyze_interval = float(values.get("analyze_interval_sec") or 0.0)
            except (TypeError, ValueError):
                analyze_interval = 0.0
            if analyze_interval < 0 or analyze_interval > 60:
                raise ValueError("Analyze interval must be between 0 and 60 seconds.")
            if not isinstance(values.get("roi"), dict):
                raise ValueError("Draw a measurement region on the video frame before starting.")
            roi = parse_roi(values.get("roi"))
            calibration = parse_calibration(
                values.get("calibration") if isinstance(values.get("calibration"), dict) else None
            )
            probe = cv2.VideoCapture(str(source))
            if not probe.isOpened():
                probe.release()
                raise RuntimeError(f"Could not open video: {source.name}")
            width = int(probe.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            height = int(probe.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            fps = float(probe.get(cv2.CAP_PROP_FPS) or 30.0) or 30.0
            frame_count = int(probe.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            duration = frame_count / fps if fps > 0 and frame_count > 0 else 0.0
            probe.release()
            warning = roi_fit_warning(roi, width, height)
            self._reset_session_locked()
            self._source = source
            self._report = report
            self._window_seconds = window
            self._analyze_interval_sec = analyze_interval
            self._roi = roi
            self._calibration = calibration
            self._history = YieldHistory(window * 1000.0)
            self._width = width
            self._height = height
            self._fps = fps
            self._frame_count = frame_count
            self._duration = duration
            self._fit_warning = warning
            self._status = "loading"
            self._running = True
            self._notice = (
                warning
                if warning
                else "CSV report is saved when you finish. Measurements use detected area, not weight."
            )
            self._generation += 1
            generation = self._generation
            self._thread = threading.Thread(
                target=self._loop,
                args=(generation,),
                name="review-live",
                daemon=True,
            )
            self._thread.start()
            return self.status()

    def action(self, name: str) -> dict[str, Any]:
        if name not in ACTIONS:
            raise ValueError("Unknown review action")
        with self._lock:
            if name == "quit":
                self._running = False
                self._generation += 1
                self._status = "saving"
            else:
                if not self._running:
                    raise RuntimeError("Start a review first.")
                self._actions.append(name)
            return self.status()

    def calibrate(self, values: dict[str, Any]) -> dict[str, Any]:
        calibration = parse_calibration(values)
        with self._lock:
            if not self._running:
                raise RuntimeError("Start a review first.")
            self._pending_calibration = calibration
            self._actions.append("calibration_changed")
            return self.status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            self._running = False
            self._generation += 1
            if self._status not in {"finished", "error"}:
                self._status = "saving"
        return self.status()

    def touch(self) -> None:
        with self._lock:
            self._connected = True
            self._last_seen = time.monotonic()

    def preview_frame(self, source_raw: str) -> dict[str, Any]:
        source = self._resolve_source(source_raw)
        cap = cv2.VideoCapture(str(source))
        if not cap.isOpened():
            cap.release()
            raise RuntimeError(f"Could not open video: {source.name}")
        try:
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            ok, frame = cap.read()
            if not ok or frame is None:
                raise RuntimeError("Could not read the first frame of that video.")
            if not width or not height:
                height, width = frame.shape[:2]
            ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            if not ok:
                raise RuntimeError("Could not encode the preview frame.")
            image = "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii")
            return {
                "source": str(source),
                "name": source.name,
                "image": image,
                "width": int(width),
                "height": int(height),
            }
        finally:
            cap.release()

    def _reset_session_locked(self) -> None:
        self._error = None
        self._paused = False
        self._slow = False
        self._complete = False
        self._position = 0.0
        self._ratio = None
        self._mass_share = None
        self._series = []
        self._image = None
        self._elapsed_ms = None
        self._pending_calibration = None
        self._actions = []
        self._complete_hist = []
        self._fit_warning = None

    def _resolve_source(self, raw: str) -> Path:
        text = str(raw or "").strip()
        if not text:
            raise ValueError("Choose a recorded video.")
        path = Path(text).expanduser()
        if not path.is_file():
            # Allow bare filenames from the kit save folder via list_videos paths.
            raise ValueError("Choose an existing video file.")
        if not is_allowed_path(path):
            raise ValueError("That video is outside the allowed storage locations.")
        suffix = path.suffix.lower()
        if suffix not in {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}:
            raise ValueError("Choose a supported video file.")
        return path.resolve()

    def _resolve_report(self, raw: str, source: Path) -> Path:
        text = str(raw or "").strip()
        if text:
            report = Path(text).expanduser()
        else:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            report = source.with_name(f"{source.stem}_review_{stamp}.csv")
        if report.suffix.lower() != ".csv":
            raise ValueError("The report path must end with .csv.")
        parent = report.parent
        if not parent.exists() or not parent.is_dir():
            raise ValueError("Choose a CSV file in an existing folder.")
        if not is_allowed_path(parent, must_be_dir=True) and not is_allowed_path(report):
            # Parent of recordings is allowed if the video itself is allowed.
            if not is_allowed_path(source):
                raise ValueError("That report folder is outside the allowed storage locations.")
        if report.exists():
            raise ValueError("The report already exists. Choose a new filename.")
        if report.resolve() == source.resolve():
            raise ValueError("The report path cannot be the video file.")
        return report.resolve()

    def _loop(self, generation: int) -> None:
        cap: cv2.VideoCapture | None = None
        last_frame = None
        meta = (self._halfyield_status().get("meta") or {}) if self._halfyield_status else {}
        weights = Path(str(self._halfyield_status().get("path") or ""))
        try:
            with self._lock:
                source = self._source
                roi = dict(self._roi)
                calibration = Calibration(
                    thresholds=dict(self._calibration.thresholds),
                    half_mass_per_area=self._calibration.half_mass_per_area,
                    piece_mass_per_area=self._calibration.piece_mass_per_area,
                )
            if source is None:
                raise RuntimeError("No source video.")
            status = self._halfyield_status()
            meta = dict(status.get("meta") or {})
            weights = Path(str(status.get("path") or ""))
            if not weights.is_file():
                raise RuntimeError("Half-yield model file is missing.")

            cap = cv2.VideoCapture(str(source))
            if not cap.isOpened():
                raise RuntimeError(f"Could not open video: {source.name}")

            with self._lock:
                if generation != self._generation:
                    return
                self._status = "reviewing"

            while True:
                with self._lock:
                    if not self._running or generation != self._generation:
                        break
                    if self._connected and time.monotonic() - self._last_seen > 20:
                        self._running = False
                        break
                    action = self._actions.pop(0) if self._actions else None
                    pending = self._pending_calibration
                    self._pending_calibration = None
                    paused = self._paused
                    slow = self._slow
                    complete = self._complete
                if pending is not None:
                    calibration = pending
                    with self._lock:
                        self._calibration = Calibration(
                            thresholds=dict(calibration.thresholds),
                            half_mass_per_area=calibration.half_mass_per_area,
                            piece_mass_per_area=calibration.piece_mass_per_area,
                        )
                        self._history.clear()
                        self._complete_hist.clear()
                        self._ratio = None
                        self._mass_share = None
                        self._series = []
                        self._notice = (
                            "Calibration applied. New report segment; resume playback to collect samples."
                        )
                    action = None

                if action in {"left", "right", "seek_back", "seek_forward", "prev_frame", "next_frame"}:
                    with self._lock:
                        self._history.clear()
                        self._complete_hist.clear()
                        self._complete = False
                        self._ratio = None
                        self._mass_share = None
                        self._series = []
                        self._notice = (
                            "New analysis segment. CSV includes playback after the most recent seek or step."
                        )
                        complete = False
                    backward = action in {"left", "seek_back", "prev_frame"}
                    step = action in {"prev_frame", "next_frame"} or (
                        paused and action in {"left", "right"}
                    )
                    if step:
                        self._step_frame(cap, -2 if backward else 0)
                        with self._lock:
                            self._paused = True
                        paused = True
                    else:
                        self._seek(cap, (-1 if backward else 1) * SKIP_SECONDS)
                    ok, frame = cap.read()
                    if ok:
                        last_frame = frame
                    continue

                if action == "toggle_pause":
                    with self._lock:
                        self._paused = True if self._complete else not self._paused
                        paused = self._paused
                    continue

                if action == "toggle_slow":
                    with self._lock:
                        self._slow = not self._slow
                        slow = self._slow
                    continue

                if action == "calibration_changed":
                    continue

                if not paused:
                    ok, frame = cap.read()
                    if not ok:
                        if last_frame is None:
                            break
                        with self._lock:
                            self._complete = True
                            self._paused = True
                            complete = True
                            paused = True
                        frame = last_frame
                    else:
                        last_frame = frame
                else:
                    if last_frame is None:
                        ok, frame = cap.read()
                        if not ok:
                            break
                        last_frame = frame
                    frame = last_frame

                pos_ms = float(cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0)
                ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                if not ok:
                    time.sleep(0.05)
                    continue
                jpeg = buf.tobytes()
                started = time.perf_counter()
                try:
                    result = self._predict_roi(
                        jpeg,
                        roi=roi,
                        thresholds=calibration.thresholds,
                        weights_path=weights,
                        meta=meta,
                        min_area_px=MIN_AREA_PX,
                        draw=True,
                    )
                except self._model_busy:
                    time.sleep(0.05)
                    continue
                except Exception as exc:  # noqa: BLE001
                    with self._lock:
                        if generation == self._generation:
                            self._error = str(exc)
                            self._status = "error"
                            self._running = False
                    break

                half_area = float(result.get("half_area") or 0.0)
                piece_area = float(result.get("piece_area") or 0.0)
                spent = time.perf_counter() - started
                with self._lock:
                    if generation != self._generation:
                        break
                    if not paused:
                        if self._history.append(pos_ms, half_area, piece_area):
                            self._complete_hist.append((pos_ms, half_area, piece_area))
                    self._ratio = self._history.current
                    self._mass_share = calibration.mass_share(self._ratio)
                    self._series = [[t, r] for t, r in self._history.points]
                    self._image = result.get("image")
                    self._elapsed_ms = int(result.get("elapsed_ms") or (spent * 1000))
                    self._position = self._duration if complete else max(0.0, pos_ms / 1000.0)
                    self._error = None
                    if result.get("roi"):
                        self._roi = dict(result["roi"])

                if paused:
                    time.sleep(0.15 if slow else 0.12)
                else:
                    with self._lock:
                        analyze_interval = float(self._analyze_interval_sec or 0.0)
                    if analyze_interval > 0:
                        target = max(analyze_interval, spent)
                    else:
                        target = (1.0 / max(1.0, self._fps)) * (4.0 if slow else 1.0)
                    if spent < target:
                        time.sleep(target - spent)
                    else:
                        # Drop unread frames covering the inference overrun so
                        # integration uses real video timestamps, not wall time.
                        self._skip_ms(cap, (spent - target) * 1000.0)

        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._error = str(exc)
                self._status = "error"
                self._running = False
        finally:
            if cap is not None:
                cap.release()
            self._finish_report(generation)

    def _skip_ms(self, cap: cv2.VideoCapture, skip_ms: float) -> None:
        if skip_ms <= 0:
            return
        end = float(cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0) + min(float(skip_ms), 1000.0)
        for _ in range(60):
            if float(cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0) >= end:
                return
            ok, _frame = cap.read()
            if not ok:
                return

    def _seek(self, cap: cv2.VideoCapture, delta_seconds: float) -> None:
        pos = float(cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0)
        next_pos = max(0.0, pos + float(delta_seconds) * 1000.0)
        cap.set(cv2.CAP_PROP_POS_MSEC, next_pos)

    def _step_frame(self, cap: cv2.VideoCapture, delta_frames: int) -> None:
        current = int(cap.get(cv2.CAP_PROP_POS_FRAMES) or 0)
        target = max(0, current + int(delta_frames))
        cap.set(cv2.CAP_PROP_POS_FRAMES, target)

    def _finish_report(self, generation: int) -> None:
        with self._lock:
            hist = list(self._complete_hist)
            calibration = Calibration(
                thresholds=dict(self._calibration.thresholds),
                half_mass_per_area=self._calibration.half_mass_per_area,
                piece_mass_per_area=self._calibration.piece_mass_per_area,
            )
            report = self._report
            source = self._source
            status = self._status
            error = self._error
            self._running = False
        if status == "error":
            return
        saved = None
        notice = "Review finished."
        if report is not None and len(hist) >= 2:
            try:
                stats = calculate_yield_statistics(hist, DEFAULT_BIN_SEC)
                stats["calibration"] = calibration.metadata()
                stats["estimated_half_mass_share"] = (
                    calibration.mass_share(stats["net_average"])
                    if stats["total_area"] > 0
                    else None
                )
                saved = save_yield_report(stats, report, video_path=source)
                notice = f"Report saved: {saved}"
            except Exception as exc:  # noqa: BLE001
                notice = f"Review finished, but the CSV could not be saved ({exc})."
        elif report is not None:
            notice = (
                "Review finished. Need at least two playback samples for a CSV report."
            )
        with self._lock:
            if generation != self._generation and self._status == "reviewing":
                return
            if self._status != "error":
                self._status = "finished"
                self._notice = notice
                if saved is not None:
                    self._report = saved
