"""On-disk Deploy live run history (summary + CSV samples)."""

from __future__ import annotations

import csv
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from storage import is_allowed_path

RUNS_DIR = Path("/mnt/recordings/deploy-runs")
RUN_ID_RE = re.compile(r"^\d{8}T\d{6}$")
_write_lock = threading.Lock()


def runs_root() -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    return RUNS_DIR


def _safe_id(run_id: str) -> str:
    text = str(run_id or "").strip()
    if not RUN_ID_RE.match(text):
        raise ValueError("Invalid deploy run id.")
    return text


def run_dir(run_id: str) -> Path:
    path = (runs_root() / _safe_id(run_id)).resolve()
    if not is_allowed_path(path, must_be_dir=True) and not is_allowed_path(path.parent, must_be_dir=True):
        raise ValueError("That run is outside allowed storage.")
    if path.parent != runs_root().resolve():
        raise ValueError("Invalid deploy run path.")
    return path


def csv_path(run_id: str) -> Path:
    path = run_dir(run_id) / "samples.csv"
    if not path.is_file():
        raise FileNotFoundError(run_id)
    return path


def _class_columns(class_names: list[str]) -> list[str]:
    cols: list[str] = []
    for name in class_names:
        safe = re.sub(r"[^A-Za-z0-9_]+", "_", str(name)).strip("_") or "class"
        cols.append(f"count_{safe}")
        cols.append(f"area_{safe}")
    return cols


def _metric_columns(metrics: list[dict[str, Any]]) -> list[str]:
    cols: list[str] = []
    for metric in metrics:
        mid = re.sub(r"[^A-Za-z0-9_]+", "_", str(metric.get("id") or "metric")).strip("_")
        cols.append(f"{mid}_value")
    return cols


def _pecan_columns() -> list[str]:
    return [
        "pecan_exposed_kernel_mean",
        "pecan_exposed_kernel_median",
        "pecan_broken_kernel_area_per_100",
        "pecan_n",
    ]


def _header(class_names: list[str], metrics: list[dict[str, Any]]) -> list[str]:
    return [
        "timestamp_iso",
        "elapsed_ms",
        "threshold",
        "speed",
        "sample_interval_sec",
        *_class_columns(class_names),
        *_metric_columns(metrics),
        *_pecan_columns(),
    ]


def open_run(
    *,
    class_names: list[str],
    metrics: list[dict[str, Any]],
    threshold: float,
    speed: str,
    interval_sec: float,
    requested_interval_sec: float,
    roi: dict[str, int] | None,
    model_filename: str | None,
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
    header = _header(list(class_names), list(metrics))
    summary = {
        "id": run_id,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "ended_at": None,
        "frames": 0,
        "threshold": threshold,
        "speed": speed,
        "interval_sec": interval_sec,
        "requested_interval_sec": requested_interval_sec,
        "roi": dict(roi) if roi else None,
        "full_frame": roi is None,
        "model_filename": model_filename,
        "class_names": list(class_names),
        "metrics": list(metrics),
        "csv": "samples.csv",
    }
    (folder / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    with (folder / "samples.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
    return summary


def append_sample(
    run_id: str,
    *,
    class_names: list[str],
    metrics: list[dict[str, Any]],
    elapsed_ms: int,
    threshold: float,
    speed: str,
    interval_sec: float,
    frame_map: dict[str, dict[str, float]],
    frame_values: dict[str, dict[str, float] | None],
    pecan_builtins: dict[str, dict[str, Any]] | None = None,
) -> None:
    folder = run_dir(run_id)
    path = folder / "samples.csv"
    row: list[Any] = [
        datetime.now(timezone.utc).isoformat(),
        int(elapsed_ms),
        float(threshold),
        str(speed),
        float(interval_sec),
    ]
    for name in class_names:
        cell = frame_map.get(str(name)) or {}
        row.append(float(cell.get("count") or 0))
        row.append(float(cell.get("area") or 0))
    for metric in metrics:
        mid = str(metric.get("id") or "")
        parts = frame_values.get(mid) if mid else None
        if not parts or parts.get("value") is None:
            row.append("")
            continue
        row.append(float(parts["value"]))
    builtins = pecan_builtins or {}
    for mid in (
        "pecan_exposed_kernel_mean",
        "pecan_exposed_kernel_median",
        "pecan_broken_kernel_area_per_100",
    ):
        card = builtins.get(mid) or {}
        if card.get("value") is not None:
            row.append(float(card["value"]))
        else:
            row.append("")
    pecan_n = 0
    for card in builtins.values():
        if isinstance(card, dict) and card.get("n") is not None:
            pecan_n = int(card.get("n") or 0)
            break
    row.append(pecan_n)
    with _write_lock:
        with path.open("a", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(row)
        summary_path = folder / "summary.json"
        try:
            summary = json.loads(summary_path.read_text())
        except (OSError, json.JSONDecodeError):
            summary = {"id": run_id}
        summary["frames"] = int(summary.get("frames") or 0) + 1
        summary_path.write_text(json.dumps(summary, indent=2) + "\n")


def finalize_run(
    run_id: str,
    *,
    frames: int,
    interval_sec: float | None = None,
    min_interval_sec: float | None = None,
    status: str = "finished",
) -> dict[str, Any]:
    folder = run_dir(run_id)
    summary_path = folder / "summary.json"
    try:
        summary = json.loads(summary_path.read_text())
    except (OSError, json.JSONDecodeError):
        summary = {"id": run_id}
    summary["status"] = status
    summary["ended_at"] = datetime.now(timezone.utc).isoformat()
    summary["frames"] = int(frames)
    if interval_sec is not None:
        summary["interval_sec"] = float(interval_sec)
    if min_interval_sec is not None:
        summary["min_interval_sec"] = float(min_interval_sec)
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def list_runs(limit: int = 50) -> list[dict[str, Any]]:
    root = runs_root()
    rows: list[dict[str, Any]] = []
    for path in sorted(root.iterdir(), reverse=True):
        if not path.is_dir():
            continue
        if not RUN_ID_RE.match(path.name):
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
                "interval_sec": summary.get("interval_sec"),
                "model_filename": summary.get("model_filename"),
                "full_frame": summary.get("full_frame"),
                "roi": summary.get("roi"),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def get_run(run_id: str, *, max_rows: int = 5000) -> dict[str, Any]:
    folder = run_dir(run_id)
    summary_path = folder / "summary.json"
    if not summary_path.is_file():
        raise FileNotFoundError(run_id)
    summary = json.loads(summary_path.read_text())
    samples: list[dict[str, Any]] = []
    csv_file = folder / "samples.csv"
    if csv_file.is_file():
        with csv_file.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for index, row in enumerate(reader):
                if index >= max_rows:
                    break
                samples.append(dict(row))
    return {"summary": summary, "samples": samples, "id": run_id}
