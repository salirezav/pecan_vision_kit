"""Server-side Deploy live inference loop that survives page navigation."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

import deploy_runs
import roi_store
from metric_formula import evaluate_formula, normalize_metric
from pecan_kpis import (
    empty_frame_samples,
    merge_run_samples,
    pecan_classes_ready,
    summarize_run,
)
HISTORY = 90
ELAPSED_WINDOW = 8
MIN_INTERVAL_SEC = 0.2
DEFAULT_INTERVAL_SEC = 1.0
SAFETY_MARGIN = 1.15


class DeployLiveService:
    def __init__(
        self,
        *,
        get_jpeg: Callable[[str], bytes | None],
        predict: Callable[..., dict[str, Any]],
        list_metrics: Callable[[], list[dict[str, Any]]],
        model_busy: type[Exception],
        model_status: Callable[[], dict[str, Any]] | None = None,
        get_frame_size: Callable[[], tuple[int | None, int | None]] | None = None,
    ) -> None:
        self._get_jpeg = get_jpeg
        self._predict = predict
        self._list_metrics = list_metrics
        self._model_busy = model_busy
        self._model_status = model_status or (lambda: {})
        self._get_frame_size = get_frame_size
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._running = False
        self._threshold = 0.5
        self._speed = "fast"
        self._requested_interval_sec = DEFAULT_INTERVAL_SEC
        self._interval_sec = DEFAULT_INTERVAL_SEC
        self._min_interval_sec: float | None = None
        self._roi: dict[str, int] | None = None
        self._roi_space: dict[str, int] | None = None
        self._elapsed_samples: list[int] = []
        self._run_id: str | None = None
        self._class_names: list[str] = []
        self._run_metrics: list[dict[str, Any]] = []
        self._run_frames = 0
        self._run_sums: dict[str, dict[str, float]] = {}
        self._histories: dict[str, list[float | None]] = {}
        self._frame_values: dict[str, dict[str, float] | None] = {}
        self._pecan_run = empty_frame_samples()
        self._pecan_builtins: dict[str, dict[str, Any]] = {}
        self._last_image: str | None = None
        self._last_elapsed_ms: int | None = None
        self._last_error: str | None = None
        self._generation = 0

    def status(self) -> dict[str, Any]:
        with self._lock:
            max_hz = None
            if self._min_interval_sec and self._min_interval_sec > 0:
                max_hz = round(1.0 / self._min_interval_sec, 3)
            return {
                "running": self._running,
                "threshold": self._threshold,
                "speed": self._speed,
                "frames": self._run_frames,
                "elapsed_ms": self._last_elapsed_ms,
                "error": self._last_error,
                "image": self._last_image,
                "run_sums": {key: dict(val) for key, val in self._run_sums.items()},
                "histories": {key: list(vals) for key, vals in self._histories.items()},
                "frame_values": {
                    key: (dict(val) if val is not None else None)
                    for key, val in self._frame_values.items()
                },
                "metrics": self._list_metrics(),
                "pecan_ready": pecan_classes_ready(self._class_names),
                "pecan_builtins": {
                    key: dict(val) for key, val in self._pecan_builtins.items()
                },
                "interval_sec": self._interval_sec,
                "requested_interval_sec": self._requested_interval_sec,
                "min_interval_sec": self._min_interval_sec,
                "max_hz": max_hz,
                "roi": dict(self._roi) if self._roi else None,
                "full_frame": self._roi is None,
                "run_id": self._run_id,
            }

    def start(
        self,
        threshold: float,
        speed: str,
        *,
        interval_sec: float = DEFAULT_INTERVAL_SEC,
    ) -> dict[str, Any]:
        with self._lock:
            if self._running:
                return self.status()
            self._threshold = min(0.95, max(0.05, float(threshold)))
            self._speed = speed if speed in {"fast", "balanced", "accurate"} else "fast"
            requested = float(interval_sec)
            if requested < MIN_INTERVAL_SEC:
                requested = MIN_INTERVAL_SEC
            self._requested_interval_sec = requested
            self._interval_sec = requested
            self._min_interval_sec = None
            self._elapsed_samples = []
            self._roi = roi_store.load_roi()
            self._roi_space = None
            if self._roi is not None and self._get_frame_size is not None:
                try:
                    width, height = self._get_frame_size()
                except Exception:  # noqa: BLE001
                    width, height = None, None
                if width and height:
                    self._roi_space = {"width": int(width), "height": int(height)}
            model = self._model_status() or {}
            self._class_names = [str(name) for name in (model.get("class_names") or [])]
            self._run_metrics = list(self._list_metrics())
            self._reset_locked()
            summary = deploy_runs.open_run(
                class_names=self._class_names,
                metrics=self._run_metrics,
                threshold=self._threshold,
                speed=self._speed,
                interval_sec=self._interval_sec,
                requested_interval_sec=self._requested_interval_sec,
                roi=self._roi,
                model_filename=str(model.get("filename") or "") or None,
            )
            self._run_id = str(summary.get("id") or "")
            self._running = True
            self._last_error = None
            self._generation += 1
            generation = self._generation
            self._thread = threading.Thread(
                target=self._loop,
                args=(generation,),
                name="deploy-live",
                daemon=True,
            )
            self._thread.start()
            return self.status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            was_running = self._running
            run_id = self._run_id
            frames = self._run_frames
            interval = self._interval_sec
            min_interval = self._min_interval_sec
            self._running = False
            self._generation += 1
        if was_running and run_id:
            try:
                deploy_runs.finalize_run(
                    run_id,
                    frames=frames,
                    interval_sec=interval,
                    min_interval_sec=min_interval,
                    status="finished",
                )
            except Exception:  # noqa: BLE001
                pass
        return self.status()

    def reset(self) -> dict[str, Any]:
        with self._lock:
            if self._running:
                raise RuntimeError("Stop the live run before resetting charts.")
            self._reset_locked()
            return self.status()

    def _reset_locked(self) -> None:
        self._run_frames = 0
        self._run_sums = {}
        self._histories = {}
        self._frame_values = {}
        self._pecan_run = empty_frame_samples()
        self._pecan_builtins = {}
        self._last_image = None
        self._last_elapsed_ms = None
        self._last_error = None

    def _update_rate_locked(self, elapsed_ms: int) -> None:
        self._elapsed_samples.append(int(elapsed_ms))
        if len(self._elapsed_samples) > ELAPSED_WINDOW:
            del self._elapsed_samples[0 : len(self._elapsed_samples) - ELAPSED_WINDOW]
        floor = (max(self._elapsed_samples) / 1000.0) * SAFETY_MARGIN
        if floor < MIN_INTERVAL_SEC:
            floor = MIN_INTERVAL_SEC
        self._min_interval_sec = floor
        self._interval_sec = max(self._requested_interval_sec, floor)

    def _wait_interval(self, generation: int, started: float) -> None:
        while True:
            with self._lock:
                if not self._running or generation != self._generation:
                    return
                interval = self._interval_sec
            remaining = interval - (time.monotonic() - started)
            if remaining <= 0:
                return
            time.sleep(min(0.1, remaining))

    def _loop(self, generation: int) -> None:
        while True:
            with self._lock:
                if not self._running or generation != self._generation:
                    return
                threshold = self._threshold
                speed = self._speed
                roi = dict(self._roi) if self._roi else None
                roi_space = dict(self._roi_space) if self._roi_space else None
            sample_started = time.monotonic()
            try:
                jpeg = self._get_jpeg(speed)
                if jpeg is None:
                    time.sleep(0.2)
                    continue
                try:
                    result = self._predict(
                        jpeg,
                        threshold,
                        speed,
                        draw=True,
                        roi=roi,
                        roi_space=roi_space,
                    )
                except self._model_busy:
                    time.sleep(0.05)
                    continue
                if not result.get("ok"):
                    with self._lock:
                        if generation == self._generation:
                            self._last_error = str(result.get("error") or "Inference failed.")
                    time.sleep(0.5)
                    continue
                self._ingest(result, generation)
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    if generation == self._generation:
                        self._last_error = str(exc)
                time.sleep(1.0)
                continue
            self._wait_interval(generation, sample_started)

    def _ingest(self, result: dict[str, Any], generation: int) -> None:
        measurements = result.get("measurements") or []
        with self._lock:
            if not self._running or generation != self._generation:
                return
            image = result.get("image")
            if image:
                self._last_image = image
            elapsed_ms = int(result.get("elapsed_ms") or 0)
            self._last_elapsed_ms = elapsed_ms
            self._update_rate_locked(elapsed_ms)
            self._last_error = None
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
                if name not in self._run_sums:
                    self._run_sums[name] = {"count": 0.0, "area": 0.0}
                self._run_sums[name]["count"] += count
                self._run_sums[name]["area"] += area
            self._run_frames += 1
            if pecan_classes_ready(self._class_names):
                frame_samples = result.get("pecan_samples")
                if isinstance(frame_samples, dict):
                    self._pecan_run = merge_run_samples(self._pecan_run, frame_samples)
                self._pecan_builtins = summarize_run(self._pecan_run)
                for mid, card in self._pecan_builtins.items():
                    history = self._histories.setdefault(mid, [])
                    value = card.get("value")
                    if card.get("ready") and value is not None and isinstance(value, (int, float)):
                        history.append(float(value))
                    else:
                        history.append(None)
                    if len(history) > HISTORY:
                        del history[0 : len(history) - HISTORY]
            metrics = self._run_metrics or self._list_metrics()
            frame_values: dict[str, dict[str, float] | None] = {}
            for metric in metrics:
                if not isinstance(metric, dict):
                    continue
                metric_id = str(metric.get("id") or "")
                if not metric_id:
                    continue
                frame = _metric_value(metric, frame_map)
                self._frame_values[metric_id] = frame
                frame_values[metric_id] = frame
                history = self._histories.setdefault(metric_id, [])
                if not frame or frame.get("value") is None:
                    history.append(None)
                else:
                    history.append(float(frame["value"]))
                if len(history) > HISTORY:
                    del history[0 : len(history) - HISTORY]
            run_id = self._run_id
            class_names = list(self._class_names)
            interval = self._interval_sec
            threshold = self._threshold
            speed = self._speed
            pecan_builtins = {
                key: dict(val) for key, val in self._pecan_builtins.items()
            }
        if run_id:
            try:
                deploy_runs.append_sample(
                    run_id,
                    class_names=class_names,
                    metrics=metrics,
                    elapsed_ms=elapsed_ms,
                    threshold=threshold,
                    speed=speed,
                    interval_sec=interval,
                    frame_map=frame_map,
                    frame_values=frame_values,
                    pecan_builtins=pecan_builtins,
                )
            except Exception:  # noqa: BLE001
                pass


def _metric_value(
    metric: dict[str, Any],
    source: dict[str, dict[str, float]],
) -> dict[str, float] | None:
    normalized = normalize_metric(metric)
    formula = str(normalized.get("formula") or "").strip()
    if not formula:
        return None
    try:
        value = evaluate_formula(formula, source)
    except ValueError:
        return None
    if value is None:
        return None
    return {"value": float(value)}
