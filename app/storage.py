"""Safe filesystem browsing + removable volume detection for save locations."""

from __future__ import annotations

import json
import os
import pwd
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from convert import list_active_jobs, read_job

VIDEO_EXTENSIONS = {".avi", ".mp4", ".mkv", ".mov", ".webm", ".m4v"}
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\- ]{0,180}$")

# Paths users may browse / pick as save folders.
BROWSE_ROOTS = (
    Path("/mnt/recordings"),
    Path("/media"),
    Path("/run/media"),
)


def _username() -> str:
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return os.environ.get("USER", "pi")


def browse_roots() -> list[Path]:
    roots = [p.resolve() for p in BROWSE_ROOTS if p.exists()]
    # Prefer user media dirs when present
    user = _username()
    for candidate in (Path("/media") / user, Path("/run/media") / user):
        if candidate.exists():
            resolved = candidate.resolve()
            if resolved not in roots:
                roots.append(resolved)
    return roots


def is_allowed_path(path: Path, *, must_be_dir: bool = False) -> bool:
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        return False
    if must_be_dir and resolved.exists() and not resolved.is_dir():
        return False
    for root in browse_roots():
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def ensure_allowed_dir(path: str | Path) -> Path:
    target = Path(path).expanduser()
    if not is_allowed_path(target, must_be_dir=False):
        raise ValueError("That folder is outside the allowed storage locations.")
    resolved = target.resolve(strict=False)
    resolved.mkdir(parents=True, exist_ok=True)
    if not resolved.is_dir():
        raise ValueError("Path is not a folder.")
    if not os.access(resolved, os.W_OK):
        raise PermissionError(f"Folder is not writable: {resolved}")
    return resolved


def _run_json(args: list[str]) -> Any:
    try:
        out = subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL, timeout=8)
        return json.loads(out)
    except (subprocess.SubprocessError, json.JSONDecodeError, FileNotFoundError):
        return None


def list_volumes() -> list[dict[str, Any]]:
    """Internal NVMe + mounted USB / removable volumes under browse roots."""
    volumes: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(path: Path, *, label: str, kind: str, device: str = "", fstype: str = "") -> None:
        if not path.exists() or not path.is_dir():
            return
        key = str(path.resolve())
        if key in seen:
            return
        if not is_allowed_path(path):
            return
        seen.add(key)
        try:
            usage = shutil.disk_usage(path)
            free_gb = round(usage.free / (1024**3), 1)
            total_gb = round(usage.total / (1024**3), 1)
        except OSError:
            free_gb = None
            total_gb = None
        volumes.append(
            {
                "path": key,
                "label": label,
                "kind": kind,
                "device": device,
                "fstype": fstype,
                "free_gb": free_gb,
                "total_gb": total_gb,
            }
        )

    add(Path("/mnt/recordings"), label="Kit storage (NVMe)", kind="internal", device="/dev/nvme0n1p1")
    videos = Path("/mnt/recordings/videos")
    if videos.exists():
        add(videos, label="Default videos folder", kind="internal")

    data = _run_json(["lsblk", "-J", "-o", "NAME,PATH,TYPE,RM,HOTPLUG,TRAN,SIZE,FSTYPE,LABEL,MOUNTPOINT"])
    if isinstance(data, dict):
        def walk(nodes: list[dict[str, Any]]) -> None:
            for node in nodes:
                mount = (node.get("mountpoint") or "").strip()
                children = node.get("children") or []
                removable = bool(node.get("rm") or node.get("hotplug") or node.get("tran") == "usb")
                if mount and mount not in {"/", "/boot", "/boot/firmware"}:
                    kind = "usb" if removable or mount.startswith(("/media/", "/run/media/")) else "other"
                    if kind in {"usb", "other"} and is_allowed_path(Path(mount)):
                        label = node.get("label") or Path(mount).name or mount
                        if kind == "usb":
                            label = f"USB — {label}"
                        add(
                            Path(mount),
                            label=label,
                            kind=kind if kind == "usb" else "mounted",
                            device=node.get("path") or "",
                            fstype=node.get("fstype") or "",
                        )
                if children:
                    walk(children)

        walk(data.get("blockdevices") or [])

    # Catch automounts under /media and /run/media
    for root in (Path("/media"), Path("/run/media")):
        if not root.exists():
            continue
        for dirpath, dirnames, _filenames in os.walk(root):
            path = Path(dirpath)
            try:
                if os.path.ismount(path) and path != root and is_allowed_path(path):
                    add(path, label=f"USB — {path.name}", kind="usb")
                    dirnames[:] = []  # do not walk into the mounted volume here
            except OSError:
                continue

    # Stable order: internal first, then USB, then others
    order = {"internal": 0, "usb": 1, "mounted": 2, "other": 3}
    volumes.sort(key=lambda v: (order.get(v["kind"], 9), v["label"].lower()))
    return volumes


def list_directory(path: str | Path) -> dict[str, Any]:
    target = Path(path).expanduser()
    if not is_allowed_path(target):
        raise ValueError("Browsing that location is not allowed.")
    resolved = target.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("Not a folder.")

    parent = resolved.parent if resolved != resolved.anchor else None
    parent_allowed = parent is not None and is_allowed_path(parent)

    folders: list[dict[str, str]] = []
    files: list[dict[str, Any]] = []
    with os.scandir(resolved) as entries:
        for entry in entries:
            name = entry.name
            if name.startswith("."):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    folders.append({"name": name, "path": str(Path(resolved) / name)})
                elif entry.is_file(follow_symlinks=False):
                    suffix = Path(name).suffix.lower()
                    if suffix in VIDEO_EXTENSIONS:
                        stat = entry.stat(follow_symlinks=False)
                        files.append(
                            {
                                "name": name,
                                "path": str(Path(resolved) / name),
                                "size_mb": round(stat.st_size / (1024**2), 1),
                            }
                        )
            except OSError:
                continue

    folders.sort(key=lambda x: x["name"].lower())
    files.sort(key=lambda x: x["name"].lower())
    return {
        "path": str(resolved),
        "parent": str(parent) if parent_allowed else None,
        "folders": folders,
        "files": files,
        "writable": os.access(resolved, os.W_OK),
    }


def list_videos(folder: str | Path, limit: int = 200) -> list[dict[str, Any]]:
    path = Path(folder)
    if not path.exists():
        return []
    videos: list[dict[str, Any]] = []
    seen: set[str] = set()

    # In-progress conversions (final .mp4 name may not exist yet)
    for job in list_active_jobs(path):
        if job.get("status") != "converting":
            continue
        name = str(job.get("name") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        source = Path(str(job.get("source") or ""))
        size_mb = 0.0
        mtime = float(job.get("started_at") or time.time())
        if source.exists():
            try:
                size_mb = round(source.stat().st_size / (1024**2), 1)
                mtime = source.stat().st_mtime
            except OSError:
                pass
        videos.append(
            {
                "name": name,
                "path": str(job.get("dest") or ""),
                "size_mb": size_mb,
                "mtime": mtime,
                "mtime_text": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime)),
                "status": "converting",
                "playable": False,
                "downloadable": False,
                "message": job.get("message")
                or "Converting to H.264 MP4 — watchable when finished.",
            }
        )

    for item in path.iterdir():
        if not item.is_file():
            continue
        name_lower = item.name.lower()
        # Ignore ffmpeg temp outputs and status sidecars
        if ".partial." in name_lower or name_lower.endswith(".converting.json"):
            continue
        if item.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        # Hide raw AVI while/after queued conversion to matching MP4
        if item.suffix.lower() == ".avi":
            dest = item.with_suffix(".mp4")
            job = read_job(dest)
            if job and job.get("status") == "converting":
                continue
            if dest.exists():
                continue
        if item.name in seen:
            continue
        try:
            stat = item.stat()
        except OSError:
            continue
        suffix = item.suffix.lower()
        playable = suffix in {".mp4", ".webm", ".m4v"} or suffix == ".avi"
        videos.append(
            {
                "name": item.name,
                "path": str(item),
                "size_mb": round(stat.st_size / (1024**2), 1),
                "mtime": stat.st_mtime,
                "mtime_text": time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)
                ),
                "status": "ready",
                "playable": playable,
                "downloadable": True,
                "message": "",
            }
        )
        seen.add(item.name)

    videos.sort(key=lambda v: v["mtime"], reverse=True)
    return videos[:limit]


def validate_video_name(name: str) -> str:
    base = Path(name).name
    if base != name or "/" in name or "\\" in name or name in {".", ".."}:
        raise ValueError("Invalid file name.")
    if not SAFE_NAME.match(base):
        raise ValueError("Use letters, numbers, spaces, dots, dashes, or underscores.")
    if Path(base).suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError("File must be a video type (.avi, .mp4, …).")
    return base


def video_path_in_folder(folder: str | Path, name: str) -> Path:
    safe_name = validate_video_name(name)
    folder_path = Path(folder).resolve()
    if not is_allowed_path(folder_path):
        raise ValueError("Save folder is not allowed.")
    path = (folder_path / safe_name).resolve()
    try:
        path.relative_to(folder_path)
    except ValueError as exc:
        raise ValueError("Invalid video path.") from exc
    return path


def _ffprobe_video_codec(path: Path) -> str:
    try:
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "csv=p=0",
                str(path),
            ],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
        return out.strip().lower()
    except (subprocess.SubprocessError, FileNotFoundError):
        return ""


def is_browser_native_video(path: Path) -> bool:
    """Chromium can play MP4/H.264 and WebM; AVI/MJPEG generally cannot."""
    suffix = path.suffix.lower()
    codec = _ffprobe_video_codec(path)
    if suffix == ".webm" and codec in {"vp8", "vp9", "av1"}:
        return True
    if suffix in {".mp4", ".m4v"} and codec in {"h264", "avc1", "hev1", "h265", "av1"}:
        return True
    return False


def preview_cache_path(source: Path) -> Path:
    cache_dir = source.parent / ".previews"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{source.name}.mp4"


def ensure_browser_playable(source: Path) -> Path:
    """
    Return a path Chromium can play. AVI/MJPEG (our recorder default) is
    remuxed/transcoded once to H.264 MP4 and cached beside the original.
    """
    source = Path(source)
    if not source.exists():
        raise FileNotFoundError("Video not found.")
    if is_browser_native_video(source):
        return source

    preview = preview_cache_path(source)
    try:
        if preview.exists() and preview.stat().st_mtime >= source.stat().st_mtime:
            return preview
    except OSError:
        pass

    preview.parent.mkdir(parents=True, exist_ok=True)
    # Must end in .mp4 so ffmpeg selects the MP4 muxer.
    tmp = preview.with_name(preview.name + ".partial.mp4")
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
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(tmp),
    ]
    try:
        subprocess.check_call(cmd, timeout=600)
        tmp.replace(preview)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return preview


def delete_preview_for(source: Path) -> None:
    preview = preview_cache_path(Path(source))
    try:
        preview.unlink(missing_ok=True)
    except OSError:
        pass


def estimate_mjpg_write_mib_s(width: int, height: int, fps: int) -> float:
    """
    Conservative estimate of sustained write rate for OpenCV MJPG .avi.

    MJPEG size varies with scene detail; use ~0.22 bytes/pixel/frame which
    tracked slightly above measured pecan-line footage on this kit.
    """
    bytes_per_pixel = 0.22
    return (float(width) * float(height) * float(fps) * bytes_per_pixel) / (1024.0 * 1024.0)


def detect_fstype(path: Path) -> str:
    try:
        out = subprocess.check_output(
            ["findmnt", "-n", "-o", "FSTYPE", "--target", str(path)],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        return out.strip().lower()
    except (subprocess.SubprocessError, FileNotFoundError):
        return ""


def detect_transport(path: Path) -> str:
    """Best-effort: nvme / usb / mmc / unknown for the block device behind path."""
    try:
        source = subprocess.check_output(
            ["findmnt", "-n", "-o", "SOURCE", "--target", str(path)],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return "unknown"
    # SOURCE like /dev/sda1 or /dev/nvme0n1p1
    base = source
    for _ in range(3):
        data = _run_json(["lsblk", "-J", "-o", "NAME,PATH,TYPE,TRAN,PKNAME", base])
        if not isinstance(data, dict):
            break
        nodes = data.get("blockdevices") or []
        if not nodes:
            break
        node = nodes[0]
        tran = (node.get("tran") or "").strip()
        if tran:
            return tran
        if node.get("type") == "disk":
            name = node.get("name") or ""
            if name.startswith("nvme"):
                return "nvme"
            if name.startswith("mmc"):
                return "mmc"
            return "unknown"
        pk = node.get("pkname")
        if not pk:
            break
        base = f"/dev/{pk}"
    if "nvme" in source:
        return "nvme"
    return "unknown"


def benchmark_write_mib_s(folder: Path, size_mib: int = 48) -> dict[str, Any]:
    """Sequential write probe with fsync; deletes the temp file afterward."""
    folder = Path(folder)
    if not folder.exists():
        raise FileNotFoundError(f"Folder does not exist: {folder}")
    size_bytes = max(8, int(size_mib)) * 1024 * 1024
    chunk = 1024 * 1024
    probe = folder / f".pecan-speed-probe-{os.getpid()}-{time.time_ns()}.bin"
    written = 0
    started = time.perf_counter()
    try:
        with open(probe, "wb", buffering=0) as handle:
            block = b"\0" * chunk
            while written < size_bytes:
                to_write = min(chunk, size_bytes - written)
                handle.write(block if to_write == chunk else block[:to_write])
                written += to_write
            handle.flush()
            os.fsync(handle.fileno())
        elapsed = max(time.perf_counter() - started, 1e-6)
        mib_s = (written / (1024 * 1024)) / elapsed
        return {
            "measured_mib_s": round(mib_s, 1),
            "probe_mib": round(written / (1024 * 1024), 1),
            "elapsed_s": round(elapsed, 2),
        }
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass


def analyze_storage_for_recording(
    folder: str | Path,
    *,
    width: int,
    height: int,
    fps: int,
    run_benchmark: bool = True,
) -> dict[str, Any]:
    """
    Compare estimated recording bitrate to measured disk write speed.

    Returns level: ok | warn | fail, plus human hints.
    """
    path = ensure_allowed_dir(folder)
    required = estimate_mjpg_write_mib_s(width, height, fps)
    # Sustained headroom: recording shares disk with preview/OS.
    headroom = 1.5
    target = required * headroom

    usage = shutil.disk_usage(path)
    free_gb = usage.free / (1024**3)
    fstype = detect_fstype(path)
    transport = detect_transport(path)

    measured = None
    probe: dict[str, Any] = {}
    if run_benchmark:
        try:
            probe = benchmark_write_mib_s(path)
            measured = float(probe["measured_mib_s"])
        except OSError as exc:
            probe = {"error": str(exc)}

    hints: list[str] = []
    reasons: list[str] = []
    level = "ok"

    if free_gb < 2:
        level = "fail"
        reasons.append(f"Only {free_gb:.1f} GB free — need more space before recording.")
        hints.append("Delete old videos or choose another drive with more free space.")
    elif free_gb < 10:
        if level == "ok":
            level = "warn"
        reasons.append(f"Low free space ({free_gb:.1f} GB). Long recordings may fill the drive.")
        hints.append("Free up space or pick a larger drive for long sessions.")

    if fstype in {"vfat", "msdos", "exfat"}:
        if level == "ok":
            level = "warn"
        reasons.append(f"Filesystem is {fstype}, which is slower/less reliable for high-rate video.")
        hints.append("Prefer the kit NVMe (ext4) or an ext4-formatted USB SSD for heavy recording.")

    if transport == "usb" and fps >= 120 and width >= 1280:
        if level == "ok":
            level = "warn"
        reasons.append("USB storage with high resolution/FPS is often too slow.")
        hints.append("Use kit storage (NVMe), or lower FPS to 60 and resolution to 1280×720.")

    if measured is not None:
        if measured < required:
            level = "fail"
            reasons.append(
                f"Measured write speed {measured:.1f} MiB/s is below the estimated need "
                f"{required:.1f} MiB/s for {width}×{height}@{fps}fps MJPG."
            )
        elif measured < target:
            if level != "fail":
                level = "warn"
            reasons.append(
                f"Measured write speed {measured:.1f} MiB/s is only marginally above the "
                f"estimated need ({required:.1f} MiB/s). Dropped frames are likely."
            )
        if level in {"warn", "fail"}:
            hints.extend(
                _speed_hints(width=width, height=height, fps=fps, transport=transport)
            )
    elif run_benchmark and probe.get("error"):
        if level == "ok":
            level = "warn"
        reasons.append(f"Could not run speed test: {probe['error']}")
        hints.append("Check that the folder is writable, then try Analyze again.")

    # Deduplicate hints while preserving order
    seen_hints: set[str] = set()
    unique_hints = []
    for hint in hints:
        if hint not in seen_hints:
            seen_hints.add(hint)
            unique_hints.append(hint)

    summary = {
        "ok": level == "ok",
        "level": level,  # ok | warn | fail
        "path": str(path),
        "width": width,
        "height": height,
        "fps": fps,
        "required_mib_s": round(required, 1),
        "target_mib_s": round(target, 1),
        "measured_mib_s": measured,
        "free_gb": round(free_gb, 1),
        "fstype": fstype or "unknown",
        "transport": transport,
        "reasons": reasons,
        "hints": unique_hints,
        "probe": probe,
        "message": _summary_message(level, measured, required, width, height, fps),
    }
    return summary


def _speed_hints(*, width: int, height: int, fps: int, transport: str) -> list[str]:
    hints: list[str] = []
    if fps > 60:
        hints.append(f"Lower frame rate from {fps} to 60 fps in Settings.")
    elif fps > 30:
        hints.append(f"Lower frame rate from {fps} to 30 fps in Settings.")
    if width > 1280 or height > 720:
        hints.append(f"Lower resolution from {width}×{height} to 1280×720 in Settings.")
    elif width > 640:
        hints.append("Try 640×480 if the drive is still too slow.")
    if transport == "usb":
        hints.append("Switch save folder to kit storage: /mnt/recordings/videos (Browse → Kit storage).")
        hints.append("If you must use USB, use a USB 3 SSD (not a cheap flash stick).")
    else:
        hints.append("Confirm the save folder is on the kit NVMe (/mnt/recordings), not a slow USB stick.")
    return hints


def _summary_message(
    level: str,
    measured: float | None,
    required: float,
    width: int,
    height: int,
    fps: int,
) -> str:
    setting = f"{width}×{height}@{fps}fps"
    if level == "ok":
        if measured is None:
            return f"Storage looks usable for {setting}."
        return f"Storage can keep up with {setting} (measured {measured:.1f} MiB/s, need ~{required:.1f})."
    if level == "warn":
        return f"Storage may struggle with {setting}. Review the hints before long recordings."
    return f"Storage is too slow or unsuitable for {setting}. Change settings or save location."
