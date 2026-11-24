"""Server-side Deploy live inference loop that survives page navigation."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

import calibration_store
import deploy_runs
import roi_store
from metric_formula import evaluate_formula, normalize_metric
from pecan_kpis import (
    BUILTIN_IDS,
    HALFYIELD_BUILTIN_ID,
    empty_frame_samples,
    halfyield_builtin_metric,
    merge_run_samples,
    pecan_classes_ready,
    summarize_run,
)
from yield_measure import (
    Calibration,
    area_share,
    integrated_area_totals,
    is_halfyield_classes,
)

HISTORY = 90
ELAPSED_WINDOW = 8
MIN_INTERVAL_SEC = 0.2
DEFAULT_INTERVAL_SEC = 1.0
SAFETY_MARGIN = 1.15
# Match the Deploy page poll so most samples stay metrics-only.
DRAW_INTERVAL_SEC = 1.0
HALFYIELD_METRIC_ID = HALFYIELD_BUILTIN_ID


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
        get_calibration: Callable[[], dict[str, Any]] | None = None,
        builtin_enabled: Callable[[], dict[str, bool]] | None = None,
    ) -> None:
        self._get_jpeg = get_jpeg
        self._predict = predict
        self._list_metrics = list_metrics
        self._model_busy = model_busy
        self._model_status = model_status or (lambda: {})
        self._get_frame_size = get_frame_size
        self._get_calibration = get_calibration or (lambda: {})
        self._builtin_enabled = builtin_enabled or (lambda: {})
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
        self._last_draw_at = 0.0
        self._halfyield = False
        self._model_id = ""
        self._yield_samples: list[tuple[float, float, float]] = []
        self._half_share_run: float | None = None
        self._half_share_frame: float | None = None
        self._mass_share_run: float | None = None
        self._mass_share_frame: float | None = None
        self._half_coef = calibration_store.DEFAULT_MASS_PER_AREA
        self._piece_coef = calibration_store.DEFAULT_MASS_PER_AREA
        self._calibration: dict[str, Any] = {}
        self._builtins_on: dict[str, bool] = {}
        self._halfyield_kpi_on = True
        self._pecan_kpi_ids: list[str] = []

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def status(self) -> dict[str, Any]:
        with self._lock:
            max_hz = None
            if self._min_interval_sec and self._min_interval_sec > 0:
                max_hz = round(1.0 / self._min_interval_sec, 3)
            metrics = list(self._run_metrics) if self._run_metrics else None
            class_names = list(self._class_names)
            halfyield = self._halfyield
            pecan_ids = list(self._pecan_kpi_ids)
            builtins_on = dict(self._builtins_on)
            payload = {
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
                "metrics": metrics,
                "pecan_builtins": {
                    key: dict(val)
                    for key, val in self._pecan_builtins.items()
                    if (not pecan_ids) or key in pecan_ids
                },
                "interval_sec": self._interval_sec,
                "requested_interval_sec": self._requested_interval_sec,
                "min_interval_sec": self._min_interval_sec,
                "max_hz": max_hz,
                "roi": dict(self._roi) if self._roi else None,
                "full_frame": self._roi is None,
                "run_id": self._run_id,
                "halfyield": halfyield,
                "calibration": dict(self._calibration) if self._calibration else None,
                "half_share": self._half_share_run if self._halfyield_kpi_on else None,
                "half_share_frame": self._half_share_frame if self._halfyield_kpi_on else None,
                "mass_share": self._mass_share_run if self._halfyield_kpi_on else None,
                "mass_share_frame": self._mass_share_frame if self._halfyield_kpi_on else None,
                "builtin_enabled": builtins_on,
            }
        if not payload["builtin_enabled"]:
            try:
                payload["builtin_enabled"] = dict(self._builtin_enabled() or {})
            except Exception:  # noqa: BLE001
                payload["builtin_enabled"] = {}
        if not class_names:
            model = self._model_status() or {}
            class_names = [str(name) for name in (model.get("class_names") or [])]
            halfyield = bool(model.get("halfyield") or is_halfyield_classes(class_names))
            payload["halfyield"] = halfyield
        enabled_pecan = [
            mid
            for mid in BUILTIN_IDS
            if payload["builtin_enabled"].get(mid, True) is not False
        ]
        payload["pecan_ready"] = (
            False if halfyield or not enabled_pecan else pecan_classes_ready(class_names)
        )
        halfyield_on = payload["builtin_enabled"].get(HALFYIELD_METRIC_ID, True) is not False
        if payload["metrics"] is None and not payload["halfyield"]:
            payload["metrics"] = self._list_metrics()
        if payload["halfyield"]:
            payload["metrics"] = (
                [halfyield_builtin_metric(enabled=True)] if halfyield_on else []
            )
            if payload["calibration"] is None:
                try:
                    payload["calibration"] = self._get_calibration()
                except Exception:  # noqa: BLE001
                    payload["calibration"] = None
        return payload

    def start(
        self,
        threshold: float = 0.5,
        speed: str = "fast",
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
            if self._get_frame_size is not None:
                try:
                    width, height = self._get_frame_size()
                except Exception:  # noqa: BLE001
                    width, height = None, None
                if width and height:
                    self._roi_space = {"width": int(width), "height": int(height)}
            model = self._model_status() or {}
            self._class_names = [str(name) for name in (model.get("class_names") or [])]
            self._halfyield = bool(
                model.get("halfyield") or is_halfyield_classes(self._class_names)
            )
            self._model_id = str(model.get("active_id") or model.get("filename") or "")
            try:
                self._builtins_on = dict(self._builtin_enabled() or {})
            except Exception:  # noqa: BLE001
                self._builtins_on = {}
            self._halfyield_kpi_on = self._builtins_on.get(HALFYIELD_METRIC_ID, True) is not False
            self._pecan_kpi_ids = [
                mid for mid in BUILTIN_IDS if self._builtins_on.get(mid, True) is not False
            ]
            if self._halfyield:
                self._speed = "accurate"
                self._run_metrics = (
                    [halfyield_builtin_metric(enabled=True)]
                    if self._halfyield_kpi_on
                    else []
                )
            else:
                self._run_metrics = list(self._list_metrics())
            try:
                self._calibration = dict(self._get_calibration() or {})
            except Exception:  # noqa: BLE001
                self._calibration = calibration_store.load(
                    self._model_id, self._class_names
                )
            half_coef, piece_coef = calibration_store.mass_coefficients(self._calibration)
            self._half_coef = half_coef
            self._piece_coef = piece_coef
            conf = self._calibration.get("class_confidence") or {}
            if conf:
                self._threshold = min(float(v) for v in conf.values())
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
                halfyield=self._halfyield,
            )
            self._run_id = str(summary.get("id") or "")
            self._running = True
            self._last_error = None
            self._last_draw_at = 0.0
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

    def refresh_calibration(self) -> dict[str, Any]:
        """Reload mass coefficients and recompute stored half-yield share."""
        with self._lock:
            try:
                self._calibration = dict(self._get_calibration() or {})
            except Exception:  # noqa: BLE001
                self._calibration = calibration_store.load(
                    self._model_id, self._class_names
                )
            half_coef, piece_coef = calibration_store.mass_coefficients(self._calibration)
            self._half_coef = half_coef
            self._piece_coef = piece_coef
            conf = self._calibration.get("class_confidence") or {}
            if conf:
                self._threshold = min(float(v) for v in conf.values())
            if self._halfyield and self._yield_samples:
                self._recompute_mass_locked()
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
        self._yield_samples = []
        self._half_share_run = None
        self._half_share_frame = None
        self._mass_share_run = None
        self._mass_share_frame = None

    def _recompute_mass_locked(self) -> None:
        if not self._halfyield_kpi_on:
            self._half_share_run = None
            self._half_share_frame = None
            self._mass_share_run = None
            self._mass_share_frame = None
            self._histories.pop(HALFYIELD_METRIC_ID, None)
            self._frame_values.pop(HALFYIELD_METRIC_ID, None)
            return
        cal = Calibration(
            half_mass_per_area=self._half_coef,
            piece_mass_per_area=self._piece_coef,
        )
        half_int, piece_int = integrated_area_totals(self._yield_samples)
        run_share = area_share(half_int, piece_int)
        self._half_share_run = run_share
        self._mass_share_run = cal.mass_share(run_share)
        history: list[float | None] = []
        for _t, half, piece in self._yield_samples:
            history.append(area_share(half, piece))
        # Keep only the last HISTORY points for the chart.
        if len(history) > HISTORY:
            history = history[-HISTORY:]
        self._histories[HALFYIELD_METRIC_ID] = history
        if self._yield_samples:
            last_half = self._yield_samples[-1][1]
            last_piece = self._yield_samples[-1][2]
            self._half_share_frame = area_share(last_half, last_piece)
            self._mass_share_frame = cal.mass_share(self._half_share_frame)
        self._frame_values[HALFYIELD_METRIC_ID] = (
            {"value": float(self._half_share_frame)}
            if self._half_share_frame is not None
            else None
        )

    def _update_rate_locked(self, elapsed_ms: int) -> None:
        self._elapsed_samples.append(int(elapsed_ms))
        if len(self._elapsed_samples) > ELAPSED_WINDOW:
            del self._elapsed_samples[0 : len(self._elapsed_samples) - ELAPSED_WINDOW]
        # Use a high percentile instead of the max so one slow frame does not
        # pin the live rate for the whole window (important on a throttled Pi).
        ordered = sorted(self._elapsed_samples)
        index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.75))))
        typical_ms = float(ordered[index])
        floor = (typical_ms / 1000.0) * SAFETY_MARGIN
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
                speed = self._speed
                roi = dict(self._roi) if self._roi else None
                roi_space = dict(self._roi_space) if self._roi_space else None
                halfyield = self._halfyield
            sample_started = time.monotonic()
            try:
                # Fresh calibration / enable flags every sample so Model edits apply live.
                try:
                    calibration = self._get_calibration()
                except Exception:  # noqa: BLE001
                    calibration = None
                try:
                    builtins_on = dict(self._builtin_enabled() or {})
                except Exception:  # noqa: BLE001
                    builtins_on = None
                if builtins_on is not None:
                    with self._lock:
                        if generation == self._generation:
                            self._builtins_on = builtins_on
                            self._halfyield_kpi_on = (
                                builtins_on.get(HALFYIELD_METRIC_ID, True) is not False
                            )
                            self._pecan_kpi_ids = [
                                mid
                                for mid in BUILTIN_IDS
                                if builtins_on.get(mid, True) is not False
                            ]
                            if self._halfyield:
                                self._run_metrics = (
                                    [halfyield_builtin_metric(enabled=True)]
                                    if self._halfyield_kpi_on
                                    else []
                                )
                            else:
                                self._run_metrics = list(self._list_metrics())
                jpeg = self._get_jpeg("accurate" if halfyield else speed)
                if jpeg is None:
                    time.sleep(0.2)
                    continue
                # Encode an overlay about as often as the page polls (~1 Hz).
                draw = (sample_started - self._last_draw_at) >= DRAW_INTERVAL_SEC
                try:
                    result = self._predict(
                        jpeg,
                        0.5,
                        "accurate" if halfyield else speed,
                        draw=draw,
                        roi=roi,
                        roi_space=roi_space,
                        calibration=calibration,
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
                if draw:
                    self._last_draw_at = sample_started
                self._ingest(result, generation, calibration)
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    if generation == self._generation:
                        self._last_error = str(exc)
                time.sleep(1.0)
                continue
            self._wait_interval(generation, sample_started)

    def _ingest(
        self,
        result: dict[str, Any],
        generation: int,
        calibration: dict[str, Any] | None,
    ) -> None:
        measurements = result.get("measurements") or []
        with self._lock:
            if not self._running or generation != self._generation:
                return
            if calibration:
                self._calibration = dict(calibration)
                half_coef, piece_coef = calibration_store.mass_coefficients(calibration)
                self._half_coef = half_coef
                self._piece_coef = piece_coef
                conf = calibration.get("class_confidence") or {}
                if conf:
                    self._threshold = min(float(v) for v in conf.values())
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

            halfyield_payload: dict[str, Any] | None = None
            if self._halfyield or result.get("halfyield"):
                half_area = float(result.get("half_area") or 0)
                piece_area = float(result.get("piece_area") or 0)
                metrics = self._run_metrics
                frame_values: dict[str, dict[str, float] | None] = {}
                pecan_builtins: dict[str, dict[str, Any]] = {}
                if self._halfyield_kpi_on:
                    now_ms = time.time() * 1000.0
                    if self._yield_samples and now_ms <= self._yield_samples[-1][0]:
                        now_ms = self._yield_samples[-1][0] + 1.0
                    self._yield_samples.append((now_ms, half_area, piece_area))
                    cal = Calibration(
                        half_mass_per_area=self._half_coef,
                        piece_mass_per_area=self._piece_coef,
                    )
                    frame_share = area_share(half_area, piece_area)
                    self._half_share_frame = frame_share
                    self._mass_share_frame = cal.mass_share(frame_share)
                    half_int, piece_int = integrated_area_totals(self._yield_samples)
                    run_share = area_share(half_int, piece_int)
                    self._half_share_run = run_share
                    self._mass_share_run = cal.mass_share(run_share)
                    history = self._histories.setdefault(HALFYIELD_METRIC_ID, [])
                    history.append(
                        float(self._half_share_frame)
                        if self._half_share_frame is not None
                        else None
                    )
                    if len(history) > HISTORY:
                        del history[0 : len(history) - HISTORY]
                    frame_value = (
                        {"value": float(self._half_share_frame)}
                        if self._half_share_frame is not None
                        else None
                    )
                    self._frame_values[HALFYIELD_METRIC_ID] = frame_value
                    frame_values = {HALFYIELD_METRIC_ID: frame_value}
                    halfyield_payload = {
                        "half_area": half_area,
                        "piece_area": piece_area,
                        "half_share": self._half_share_frame,
                        "half_share_run": self._half_share_run,
                        "mass_share": self._mass_share_frame,
                        "mass_share_run": self._mass_share_run,
                    }
                else:
                    self._half_share_frame = None
                    self._half_share_run = None
                    self._mass_share_frame = None
                    self._mass_share_run = None
                    halfyield_payload = {
                        "half_area": half_area,
                        "piece_area": piece_area,
                        "half_share": None,
                        "half_share_run": None,
                        "mass_share": None,
                        "mass_share_run": None,
                    }
            else:
                if self._pecan_kpi_ids and pecan_classes_ready(self._class_names):
                    frame_samples = result.get("pecan_samples")
                    if isinstance(frame_samples, dict):
                        merge_run_samples(self._pecan_run, frame_samples)
                    summarized = summarize_run(self._pecan_run)
                    self._pecan_builtins = {
                        mid: dict(card)
                        for mid, card in summarized.items()
                        if mid in self._pecan_kpi_ids
                    }
                    for mid, card in self._pecan_builtins.items():
                        history = self._histories.setdefault(mid, [])
                        value = card.get("value")
                        # Chart the preview even before N≥200 so panels are not blank.
                        if value is not None and isinstance(value, (int, float)):
                            history.append(float(value))
                        else:
                            history.append(None)
                        if len(history) > HISTORY:
                            del history[0 : len(history) - HISTORY]
                else:
                    self._pecan_builtins = {}
                metrics = self._run_metrics or self._list_metrics()
                frame_values = {}
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
                pecan_builtins = {
                    key: dict(val) for key, val in self._pecan_builtins.items()
                }
            run_id = self._run_id
            class_names = list(self._class_names)
            interval = self._interval_sec
            threshold = self._threshold
            speed = self._speed
            halfyield = self._halfyield
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
                    halfyield=halfyield_payload if halfyield else None,
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
