"""Post-record conversion of MJPEG AVI → H.264 MP4 (smaller + browser-playable)."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}


def _job_key(path: Path) -> str:
    return str(path.resolve())


def status_path_for(dest_mp4: Path) -> Path:
    return dest_mp4.with_suffix(dest_mp4.suffix + ".converting.json")


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
    key = _job_key(dest)
    status_file = status_path_for(dest)

    with _lock:
        existing = _jobs.get(key)
        if existing and existing.get("status") == "converting":
            return dict(existing)

        job: dict[str, Any] = {
            "status": "converting",
            "source": str(source),
            "dest": str(dest),
            "name": dest.name,
            "source_name": source.name,
            "crf": int(crf),
            "preset": preset,
            "started_at": time.time(),
            "finished_at": None,
            "error": None,
            "message": "Converting to H.264 MP4 — video will be watchable when this finishes.",
        }
        _jobs[key] = job
        status_file.write_text(json.dumps(job, indent=2))

    thread = threading.Thread(
        target=_run_convert,
        args=(source, dest, status_file, key, int(crf), preset, delete_source),
        name=f"convert-{dest.name}",
        daemon=True,
    )
    thread.start()
    return dict(job)


def _run_convert(
    source: Path,
    dest: Path,
    status_file: Path,
    key: str,
    crf: int,
    preset: str,
    delete_source: bool,
) -> None:
    tmp = dest.with_name(dest.name + ".partial.mp4")
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
    try:
        subprocess.check_call(cmd, timeout=3600)
        tmp.replace(dest)
        if delete_source:
            try:
                source.unlink(missing_ok=True)
            except OSError:
                pass
        # Drop any old browser-preview cache for the avi
        preview = source.parent / ".previews" / f"{source.name}.mp4"
        try:
            preview.unlink(missing_ok=True)
        except OSError:
            pass
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass

    finished = time.time()
    with _lock:
        job = _jobs.get(key, {})
        if error:
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

    try:
        if error:
            status_file.write_text(json.dumps(final, indent=2))
        else:
            status_file.unlink(missing_ok=True)
    except OSError:
        pass
