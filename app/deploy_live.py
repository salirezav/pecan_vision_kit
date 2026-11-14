"""Server-side Deploy live inference loop that survives page navigation."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable


HISTORY = 90


class DeployLiveService:
    def __init__(
        self,
        *,
        get_jpeg: Callable[[str], bytes | None],
        predict: Callable[..., dict[str, Any]],
        list_metrics: Callable[[], list[dict[str, Any]]],
        model_busy: type[Exception],
    ) -> None:
        self._get_jpeg = get_jpeg
        self._predict = predict
        self._list_metrics = list_metrics
        self._model_busy = model_busy
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._running = False
        self._threshold = 0.5
        self._speed = "fast"
        self._run_frames = 0
        self._run_sums: dict[str, dict[str, float]] = {}
        self._histories: dict[str, list[float | None]] = {}
        self._frame_values: dict[str, dict[str, float] | None] = {}
        self._last_image: str | None = None
        self._last_elapsed_ms: int | None = None
        self._last_error: str | None = None
        self._generation = 0

    def status(self) -> dict[str, Any]:
        with self._lock:
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
            }

    def start(self, threshold: float, speed: str) -> dict[str, Any]:
        with self._lock:
            if self._running:
                return self.status()
            self._threshold = min(0.95, max(0.05, float(threshold)))
            self._speed = speed if speed in {"fast", "balanced", "accurate"} else "fast"
            self._reset_locked()
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
            self._running = False
            self._generation += 1
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
        self._last_image = None
        self._last_elapsed_ms = None
        self._last_error = None

    def _loop(self, generation: int) -> None:
        while True:
            with self._lock:
                if not self._running or generation != self._generation:
                    return
                threshold = self._threshold
                speed = self._speed
            try:
                jpeg = self._get_jpeg(speed)
                if jpeg is None:
                    time.sleep(0.2)
                    continue
                try:
                    result = self._predict(jpeg, threshold, speed, draw=False)
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
                        # Keep running; a transient error should not kill Deploy.
                time.sleep(1.0)
                continue
            # Success path: loop immediately so FPS tracks model time.
            time.sleep(0)

    def _ingest(self, result: dict[str, Any], generation: int) -> None:
        measurements = result.get("measurements") or []
        with self._lock:
            if not self._running or generation != self._generation:
                return
            image = result.get("image")
            if image:
                self._last_image = image
            self._last_elapsed_ms = int(result.get("elapsed_ms") or 0)
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
            metrics = self._list_metrics()
            for metric in metrics:
                if not isinstance(metric, dict):
                    continue
                metric_id = str(metric.get("id") or "")
                if not metric_id:
                    continue
                frame = _ratio_parts(metric, frame_map)
                self._frame_values[metric_id] = frame
                history = self._histories.setdefault(metric_id, [])
                if not frame or not frame.get("denominator"):
                    history.append(None)
                else:
                    history.append(
                        (float(frame["numerator"]) / float(frame["denominator"])) * 100.0
                    )
                if len(history) > HISTORY:
                    del history[0 : len(history) - HISTORY]


def _sum_labels(
    source: dict[str, dict[str, float]],
    labels: list[Any],
    measure: str,
) -> float:
    total = 0.0
    for name in labels:
        row = source.get(str(name))
        if not row:
            continue
        total += float(row.get(measure) or 0)
    return total


def _ratio_parts(
    metric: dict[str, Any],
    source: dict[str, dict[str, float]],
) -> dict[str, float] | None:
    measure = str(metric.get("measure") or "count")
    numerator = _sum_labels(source, list(metric.get("numerator") or []), measure)
    denominator = _sum_labels(source, list(metric.get("denominator") or []), measure)
    return {"numerator": numerator, "denominator": denominator}
