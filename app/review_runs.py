"""On-disk Review run folders (summary + report CSV)."""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from storage import is_allowed_path

RUNS_DIR = Path("/mnt/recordings/review-runs")
RUN_ID_RE = re.compile(r"^\d{8}T\d{6}$")
_write_lock = threading.Lock()


def runs_root() -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    return RUNS_DIR


def _safe_id(run_id: str) -> str:
    text = str(run_id or "").strip()
    if not RUN_ID_RE.match(text):
        raise ValueError("Invalid review run id.")
    return text


def run_dir(run_id: str) -> Path:
    path = (runs_root() / _safe_id(run_id)).resolve()
    if not is_allowed_path(path, must_be_dir=True) and not is_allowed_path(
        path.parent, must_be_dir=True
    ):
        raise ValueError("That run is outside allowed storage.")
    if path.parent != runs_root().resolve():
        raise ValueError("Invalid review run path.")
    return path


def csv_path(run_id: str) -> Path:
    path = run_dir(run_id) / "report.csv"
    if not path.is_file():
        raise FileNotFoundError(run_id)
    return path


def open_run(
    *,
    video_name: str,
    video_path: str,
    model_filename: str | None,
    halfyield: bool,
    frames_per_sec: float,
    window_sec: float,
    roi: dict[str, int] | None,
    class_names: list[str],
) -> dict[str, Any]:
    import time as _time  # noqa: PLC0415

    root = runs_root()
    run_id = datetime.now().strftime("%Y%m%dT%H%M%S")
    for _ in range(20):
        if not (root / run_id).exists():
            break
        _time.sleep(0.05)
        run_id = datetime.now().strftime("%Y%m%dT%H%M%S")
    folder = root / run_id
    folder.mkdir(parents=True, exist_ok=False)
    summary = {
        "id": run_id,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "ended_at": None,
        "video_name": video_name,
        "video_path": video_path,
        "model_filename": model_filename,
        "halfyield": bool(halfyield),
        "frames_per_sec": float(frames_per_sec),
        "window_sec": float(window_sec),
        "roi": dict(roi) if roi else None,
        "full_frame": roi is None,
        "class_names": list(class_names),
        "frames": 0,
        "csv": "report.csv",
        "final_kpis": None,
    }
    with _write_lock:
        (folder / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def finalize_run(
    run_id: str,
    *,
    frames: int,
    status: str = "finished",
    final_kpis: dict[str, Any] | None = None,
    report_name: str = "report.csv",
) -> dict[str, Any]:
    folder = run_dir(run_id)
    summary_path = folder / "summary.json"
    with _write_lock:
        try:
            summary = json.loads(summary_path.read_text())
        except (OSError, json.JSONDecodeError):
            summary = {"id": run_id}
        summary["status"] = status
        summary["ended_at"] = datetime.now(timezone.utc).isoformat()
        summary["frames"] = int(frames)
        summary["csv"] = report_name
        if final_kpis is not None:
            summary["final_kpis"] = final_kpis
        summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def list_runs(limit: int = 50) -> list[dict[str, Any]]:
    root = runs_root()
    rows: list[dict[str, Any]] = []
    for path in sorted(root.iterdir(), reverse=True):
        if not path.is_dir() or not RUN_ID_RE.match(path.name):
            continue
        summary_path = path / "summary.json"
        if not summary_path.is_file():
            continue
        try:
            summary = json.loads(summary_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(summary, dict):
            continue
        rows.append(
            {
                "id": path.name,
                "status": summary.get("status"),
                "started_at": summary.get("started_at"),
                "ended_at": summary.get("ended_at"),
                "frames": summary.get("frames"),
                "video_name": summary.get("video_name"),
                "model_filename": summary.get("model_filename"),
                "halfyield": summary.get("halfyield"),
                "frames_per_sec": summary.get("frames_per_sec"),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def get_run(run_id: str) -> dict[str, Any]:
    folder = run_dir(run_id)
    summary_path = folder / "summary.json"
    if not summary_path.is_file():
        raise FileNotFoundError(run_id)
    summary = json.loads(summary_path.read_text())
    report = folder / "report.csv"
    return {
        "id": run_id,
        "summary": summary,
        "has_csv": report.is_file(),
    }
