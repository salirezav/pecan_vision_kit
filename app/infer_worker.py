"""Inference process: load torch only in this process, never in the web process.

The parent imports this module solely to start the spawn target. Keep torch out
of the import graph above worker_main.
"""

from __future__ import annotations

import multiprocessing as mp
import threading
from pathlib import Path
from typing import Any


def worker_main(request_q: Any, response_q: Any) -> None:
    """Spawn entry point. Imports segment_runtime only after the process starts."""
    # Delay heavy imports until we are in the child process.
    from segment_runtime import load_model, predict_frame  # noqa: PLC0415

    model = None
    loaded_path: str | None = None
    loaded_mtime: float | None = None
    loaded_meta_sig: str | None = None

    while True:
        job = request_q.get()
        if job is None or job.get("cmd") == "stop":
            break
        if job.get("cmd") != "predict":
            response_q.put({"ok": False, "error": "Unknown inference command."})
            continue
        try:
            recording = bool(job.get("recording"))
            import os  # noqa: PLC0415
            import torch  # noqa: PLC0415

            cores = max(1, os.cpu_count() or 1)
            # Leave headroom for the camera grab thread while recording.
            torch.set_num_threads(2 if recording else min(4, cores))
            weights = Path(str(job["weights_path"]))
            meta = dict(job.get("meta") or {})
            mtime = weights.stat().st_mtime
            meta_sig = repr(
                (
                    meta.get("family"),
                    meta.get("variant"),
                    meta.get("num_classes"),
                    tuple(meta.get("class_names") or []),
                )
            )
            if (
                model is None
                or loaded_path != str(weights)
                or loaded_mtime != mtime
                or loaded_meta_sig != meta_sig
            ):
                model = load_model(weights, meta)
                loaded_path = str(weights)
                loaded_mtime = mtime
                loaded_meta_sig = meta_sig
            result = predict_frame(
                model,
                job["jpeg"],
                meta,
                float(job.get("threshold", 0.5)),
                speed=str(job.get("speed") or "fast"),
                draw=bool(job.get("draw", True)),
            )
            response_q.put(result)
        except Exception as exc:  # noqa: BLE001
            response_q.put({"ok": False, "error": str(exc)})


class InferClient:
    """One-in-flight JPEG jobs against a spawn inference child."""

    def __init__(self) -> None:
        self._ctx = mp.get_context("spawn")
        self._lock = threading.Lock()
        self._busy = False
        self._proc: mp.Process | None = None
        self._request_q: Any = None
        self._response_q: Any = None

    def start(self) -> None:
        with self._lock:
            self._ensure_process_locked()

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        if self._request_q is not None:
            try:
                self._request_q.put({"cmd": "stop"})
            except Exception:  # noqa: BLE001
                pass
        if self._proc is not None:
            self._proc.join(timeout=3)
            if self._proc.is_alive():
                self._proc.terminate()
                self._proc.join(timeout=2)
        self._proc = None
        self._request_q = None
        self._response_q = None

    def _ensure_process_locked(self) -> None:
        if self._proc is not None and self._proc.is_alive():
            return
        self._stop_locked()
        self._request_q = self._ctx.Queue(maxsize=1)
        self._response_q = self._ctx.Queue(maxsize=1)
        self._proc = self._ctx.Process(
            target=worker_main,
            args=(self._request_q, self._response_q),
            name="infer-worker",
            daemon=True,
        )
        self._proc.start()

    def predict(
        self,
        jpeg: bytes,
        *,
        weights_path: Path,
        meta: dict[str, Any],
        threshold: float,
        speed: str,
        recording: bool,
        draw: bool = True,
    ) -> dict[str, Any]:
        from segment_inspect import ModelBusy  # noqa: PLC0415

        with self._lock:
            if self._busy:
                raise ModelBusy("The model is already working. Wait for it to finish.")
            self._busy = True
            try:
                self._ensure_process_locked()
                assert self._request_q is not None
                assert self._response_q is not None
                self._request_q.put(
                    {
                        "cmd": "predict",
                        "jpeg": jpeg,
                        "weights_path": str(weights_path),
                        "meta": meta,
                        "threshold": threshold,
                        "speed": speed,
                        "recording": recording,
                        "draw": bool(draw),
                    }
                )
            except Exception:
                self._busy = False
                raise
        try:
            # First load can take several minutes on the Pi.
            result = self._response_q.get(timeout=600)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._busy = False
                # Child may have died; restart on next call.
                if self._proc is not None and not self._proc.is_alive():
                    self._stop_locked()
            return {"ok": False, "error": f"Inference worker failed: {exc}"}
        with self._lock:
            self._busy = False
            if self._proc is not None and not self._proc.is_alive():
                self._stop_locked()
        if not isinstance(result, dict):
            return {"ok": False, "error": "Inference worker returned a bad result."}
        return result

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._busy
