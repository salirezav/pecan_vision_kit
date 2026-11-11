"""Shared kit runtime state and helpers used by route blueprints."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any

import yaml

from camera import FPS_CHOICES, RESOLUTIONS, CameraService, find_camera_device
from deploy_live import DeployLiveService
from segment import ModelBusy, ModelService
from storage import ensure_allowed_dir, list_videos
from update import read_version, version_label

ROOT = Path(__file__).resolve().parent.parent
DEFAULTS_PATH = ROOT / "config" / "defaults.yaml"
RUNTIME_CONFIG = Path("/mnt/recordings/config.yaml")
PRESETS_PATH = Path("/mnt/recordings/settings-presets.yaml")
SERVICE_NAME = "pecan-vision.service"
APP_VERSION = read_version(ROOT)

cam: CameraService | None = None
settings: dict[str, Any] = {}
models = ModelService(
    ROOT,
    recording_check=lambda: bool(cam and cam.status().get("recording")),
)
deploy_live = DeployLiveService(
    get_jpeg=lambda: require_cam().get_jpeg(quality=90),
    predict=lambda jpeg, threshold, speed: models.predict_jpeg(
        jpeg, threshold, speed=speed
    ),
    list_metrics=lambda: models.list_metrics(),
    model_busy=ModelBusy,
)


def load_defaults() -> dict[str, Any]:
    return yaml.safe_load(DEFAULTS_PATH.read_text()) or {}


def load_settings() -> dict[str, Any]:
    data = load_defaults()
    if RUNTIME_CONFIG.exists():
        overlay = yaml.safe_load(RUNTIME_CONFIG.read_text()) or {}
        data.update(overlay)
    return data


def recording_default_values() -> dict[str, Any]:
    data = load_defaults()
    return {
        "width": int(data.get("width", 1280)),
        "height": int(data.get("height", 720)),
        "fps": int(data.get("fps", 60)),
        "save_dir": str(data.get("save_dir", "/mnt/recordings/videos")).strip(),
    }


def save_settings(data: dict[str, Any]) -> None:
    RUNTIME_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME_CONFIG.write_text(yaml.safe_dump(data, sort_keys=False))


def apply_recording_settings(
    c: CameraService,
    width: int,
    height: int,
    fps: int,
    save_dir: str,
) -> None:
    if (width, height) not in RESOLUTIONS:
        raise ValueError("Unsupported resolution")
    if fps not in FPS_CHOICES:
        raise ValueError("Unsupported frame rate")
    resolved_save = ensure_allowed_dir(save_dir)
    c.reconfigure(
        width=width,
        height=height,
        fps=fps,
        save_dir=str(resolved_save),
    )
    settings.update(
        {
            "width": width,
            "height": height,
            "fps": fps,
            "save_dir": str(resolved_save),
        }
    )
    save_settings(settings)


def clean_preset_name(raw: str) -> str:
    name = " ".join(str(raw).split())
    if not name or len(name) > 40:
        raise ValueError("Enter a name up to 40 characters.")
    if any(ch in name for ch in "/\\"):
        raise ValueError("The name cannot include a slash.")
    return name


def load_presets() -> dict[str, Any]:
    if not PRESETS_PATH.exists():
        return {}
    data = yaml.safe_load(PRESETS_PATH.read_text()) or {}
    return data if isinstance(data, dict) else {}


def write_presets(data: dict[str, Any]) -> None:
    if not data:
        if PRESETS_PATH.exists():
            PRESETS_PATH.unlink()
        return
    PRESETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PRESETS_PATH.write_text(yaml.safe_dump(data, sort_keys=False))


def capture_preset(c: CameraService) -> dict[str, Any]:
    status = c.status()
    controls: dict[str, int] = {}
    for ctrl in c.list_v4l_controls():
        if not ctrl.get("name") or "value" not in ctrl or ctrl.get("error"):
            continue
        try:
            controls[str(ctrl["name"])] = int(ctrl["value"])
        except (TypeError, ValueError):
            continue
    return {
        "width": int(status["width"]),
        "height": int(status["height"]),
        "fps": int(status["fps"]),
        "save_dir": str(status["save_dir"]),
        "controls": controls,
    }


def apply_preset(c: CameraService, preset: dict[str, Any]) -> None:
    apply_recording_settings(
        c,
        int(preset["width"]),
        int(preset["height"]),
        int(preset["fps"]),
        str(preset.get("save_dir") or "").strip(),
    )
    raw_controls = preset.get("controls") or {}
    if isinstance(raw_controls, dict) and raw_controls:
        c.apply_v4l_values(raw_controls)


def init_camera() -> CameraService:
    global cam, settings
    settings = load_settings()
    device = find_camera_device(
        settings.get("camera_device", ""),
        settings.get("camera_device_fallback", "/dev/video0"),
    )
    settings["camera_device"] = device
    service = CameraService(
        device=device,
        width=int(settings.get("width", 1280)),
        height=int(settings.get("height", 720)),
        fps=int(settings.get("fps", 60)),
        save_dir=settings.get("save_dir", "/mnt/recordings/videos"),
        fourcc_name=settings.get("fourcc", "MJPG"),
        record_extension=settings.get("record_extension", ".avi"),
    )
    service.start()
    cam = service
    return service


def require_cam() -> CameraService:
    if cam is None:
        raise RuntimeError("Camera not initialized")
    return cam


def run_cmd(args: list[str], timeout: int = 15) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, out.strip()
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


def disk_info(path: str) -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(path)
        return {
            "path": path,
            "total_gb": round(usage.total / (1024**3), 1),
            "used_gb": round(usage.used / (1024**3), 1),
            "free_gb": round(usage.free / (1024**3), 1),
            "mounted": os.path.ismount(path) or Path(path).exists(),
        }
    except OSError as exc:
        return {"path": path, "error": str(exc)}


def network_snapshot() -> dict[str, Any]:
    code, ip_out = run_cmd(["ip", "-br", "addr"])
    _, nm_out = run_cmd(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"])
    _, wifi_out = run_cmd(["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"])
    hostname = socket.gethostname()
    return {
        "hostname": hostname,
        "ip_addr": ip_out if code == 0 else "unavailable",
        "nm_devices": nm_out,
        "active_connections": wifi_out,
    }


def current_save_dir() -> Path:
    return Path(settings.get("save_dir", "/mnt/recordings/videos"))


def apply_save_dir(path: str) -> Path:
    resolved = ensure_allowed_dir(path)
    require_cam().reconfigure(save_dir=str(resolved))
    settings["save_dir"] = str(resolved)
    save_settings(settings)
    return resolved


def list_recent_videos(limit: int = 20) -> list[dict[str, Any]]:
    return list_videos(current_save_dir(), limit=limit)


def version_context() -> dict[str, Any]:
    return {
        "app_version": APP_VERSION,
        "app_version_label": version_label(APP_VERSION),
    }
