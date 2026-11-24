"""Background ffmpeg jobs: post-record AVI→MP4 and browser preview encodes."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}
_queue: list[str] = []
_worker: threading.Thread | None = None
_active_proc: subprocess.Popen[Any] | None = None
_active_key: str | None = None
_cancel_requested: set[str] = set()


def _job_key(path: Path) -> str:
    return str(path.resolve())


def status_path_for(dest_mp4: Path) -> Path:
    return dest_mp4.with_suffix(dest_mp4.suffix + ".converting.json")


def preview_cache_path(source: Path) -> Path:
    cache_dir = source.parent / ".previews"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{source.name}.mp4"


def read_job(dest_mp4: Path) -> dict[str, Any] | None:
    key = _job_key(dest_mp4)
    with _lock:
        mem = _jobs.get(key)
        if mem:
            return dict(mem)
    status_file = status_path_for(dest_mp4)
    if not status_file.exists():
        return None
    try:
        data = json.loads(status_file.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data


def list_active_jobs(folder: Path) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    if not folder.exists():
        return jobs
    for path in folder.glob("*.mp4.converting.json"):
        try:
            data = json.loads(path.read_text())
            jobs.append(data)
        except (OSError, json.JSONDecodeError):
            continue
    previews = folder / ".previews"
    if previews.exists():
        for path in previews.glob("*.mp4.converting.json"):
            try:
                data = json.loads(path.read_text())
                jobs.append(data)
            except (OSError, json.JSONDecodeError):
                continue
    with _lock:
        for job in _jobs.values():
            dest = str(job.get("dest") or "")
            if dest.startswith(str(folder.resolve())) and job not in jobs:
                jobs.append(dict(job))
    return jobs


def queue_avi_to_mp4(
    source_avi: str | Path,
    *,
    crf: int = 20,
    preset: str = "veryfast",
    delete_source: bool = True,
) -> dict[str, Any]:
    source = Path(source_avi).resolve()
    if source.suffix.lower() != ".avi":
        raise ValueError("Conversion expects an .avi capture file")
    if not source.exists():
        raise FileNotFoundError(f"Capture file missing: {source}")

    dest = source.with_suffix(".mp4")
    return _enqueue(
        {
            "kind": "finalize",
            "source": str(source),
            "dest": str(dest),
            "name": dest.name,
            "source_name": source.name,
            "crf": int(crf),
            "preset": preset,
            "delete_source": bool(delete_source),
            "message": "Converting to H.264 MP4 — video will be watchable when this finishes.",
        }
    )


def queue_preview(source: str | Path) -> dict[str, Any]:
    """Encode a browser-playable H.264 preview beside the source; keep the source."""
    path = Path(source).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Video not found: {path}")
    dest = preview_cache_path(path)
    return _enqueue(
        {
            "kind": "preview",
            "source": str(path),
            "dest": str(dest),
            "name": path.name,
            "source_name": path.name,
            "crf": 28,
            "preset": "ultrafast",
            "delete_source": False,
            "message": "Preparing a playable preview — try again in a moment.",
        }
    )


def cancel_preview_jobs() -> int:
    """Kill in-flight and queued preview encodes. Finalize (AVI→MP4) jobs keep running."""
    killed = 0
    with _lock:
        for key, job in list(_jobs.items()):
            if job.get("kind") != "preview":
                continue
            if job.get("status") != "converting":
                continue
            _cancel_requested.add(key)
            if key in _queue:
                _queue.remove(key)
                job.update(
                    {
                        "status": "cancelled",
                        "finished_at": time.time(),
                        "error": "cancelled",
                        "message": "Preview cancelled.",
                    }
                )
                killed += 1
                status_file = status_path_for(Path(str(job["dest"])))
                try:
                    status_file.unlink(missing_ok=True)
                except OSError:
                    pass
            elif key == _active_key and _active_proc is not None:
                try:
                    _active_proc.kill()
                    killed += 1
                except OSError:
                    pass
    return killed


def _enqueue(fields: dict[str, Any]) -> dict[str, Any]:
    dest = Path(str(fields["dest"])).resolve()
    key = _job_key(dest)
    status_file = status_path_for(dest)

    with _lock:
        existing = _jobs.get(key)
        if existing and existing.get("status") == "converting":
            return dict(existing)

        job: dict[str, Any] = {
            "status": "converting",
            "started_at": time.time(),
            "finished_at": None,
            "error": None,
            **fields,
            "dest": str(dest),
        }
        _jobs[key] = job
        _cancel_requested.discard(key)
        if key not in _queue:
            _queue.append(key)
        try:
            status_file.parent.mkdir(parents=True, exist_ok=True)
            status_file.write_text(json.dumps(job, indent=2))
        except OSError:
            pass
        _ensure_worker_locked()
        return dict(job)


def _ensure_worker_locked() -> None:
    global _worker
    if _worker is not None and _worker.is_alive():
        return
    _worker = threading.Thread(target=_worker_loop, name="media-queue", daemon=True)
    _worker.start()


def _worker_loop() -> None:
    global _worker
    while True:
        with _lock:
            if not _queue:
                # Clear before release so a concurrent enqueue starts a new worker.
                _worker = None
                return
            key = _queue.pop(0)
            job = dict(_jobs.get(key) or {})
        if not job or job.get("status") != "converting":
            continue
        if key in _cancel_requested:
            _finish_job(key, error="cancelled", cancelled=True)
            continue
        _run_job(key, job)


def _run_job(key: str, job: dict[str, Any]) -> None:
    global _active_proc, _active_key
    source = Path(str(job["source"]))
    dest = Path(str(job["dest"]))
    status_file = status_path_for(dest)
    tmp = dest.with_name(dest.name + ".partial.mp4")
    kind = str(job.get("kind") or "finalize")
    crf = int(job.get("crf") or 20)
    preset = str(job.get("preset") or "veryfast")
    delete_source = bool(job.get("delete_source"))

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(tmp),
    ]
    error: str | None = None
    cancelled = False
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(cmd)
        with _lock:
            _active_proc = proc
            _active_key = key
        code = proc.wait(timeout=3600)
        if key in _cancel_requested:
            cancelled = True
            error = "cancelled"
        elif code != 0:
            error = f"ffmpeg exited with code {code}"
        else:
            tmp.replace(dest)
            if delete_source:
                try:
                    source.unlink(missing_ok=True)
                except OSError:
                    pass
            if kind == "finalize":
                preview = source.parent / ".previews" / f"{source.name}.mp4"
                try:
                    preview.unlink(missing_ok=True)
                except OSError:
                    pass
    except Exception as exc:  # noqa: BLE001
        if key in _cancel_requested:
            cancelled = True
            error = "cancelled"
        else:
            error = str(exc)
    finally:
        with _lock:
            _active_proc = None
            _active_key = None
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass

    _finish_job(key, error=error, cancelled=cancelled, status_file=status_file)


def _finish_job(
    key: str,
    *,
    error: str | None,
    cancelled: bool = False,
    status_file: Path | None = None,
) -> None:
    finished = time.time()
    with _lock:
        job = _jobs.get(key, {})
        if cancelled or error == "cancelled":
            job.update(
                {
                    "status": "cancelled",
                    "finished_at": finished,
                    "error": "cancelled",
                    "message": "Preview cancelled.",
                }
            )
        elif error:
            job.update(
                {
                    "status": "failed",
                    "finished_at": finished,
                    "error": error,
                    "message": f"Conversion failed: {error}",
                }
            )
        else:
            job.update(
                {
                    "status": "ready",
                    "finished_at": finished,
                    "error": None,
                    "message": "Conversion complete — ready to watch and download.",
                }
            )
        _jobs[key] = job
        final = dict(job)
        _cancel_requested.discard(key)
        if status_file is None and job.get("dest"):
            status_file = status_path_for(Path(str(job["dest"])))

    if status_file is None:
        return
    try:
        if final.get("status") in {"failed"}:
            status_file.write_text(json.dumps(final, indent=2))
        else:
            status_file.unlink(missing_ok=True)
    except OSError:
        pass
