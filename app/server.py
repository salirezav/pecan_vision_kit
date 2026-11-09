#!/usr/bin/env python3
"""Pecan Vision Kit — web recorder + admin UI."""

from __future__ import annotations

import mimetypes
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml
from flask import Flask, Response, jsonify, redirect, render_template, request, send_file, url_for

from camera import FPS_CHOICES, RESOLUTIONS, CameraService, find_camera_device
from segment import MAX_UPLOAD_BYTES, ModelBusy, ModelService
from convert import queue_avi_to_mp4, read_job
from storage import (
    analyze_storage_for_recording,
    delete_preview_for,
    ensure_allowed_dir,
    ensure_browser_playable,
    list_directory,
    list_videos,
    list_volumes,
    video_path_in_folder,
)
from update import check_for_update, read_version, start_apply_update, version_label

ROOT = Path(__file__).resolve().parent.parent
DEFAULTS_PATH = ROOT / "config" / "defaults.yaml"
RUNTIME_CONFIG = Path("/mnt/recordings/config.yaml")
PRESETS_PATH = Path("/mnt/recordings/settings-presets.yaml")
SERVICE_NAME = "pecan-vision.service"
APP_VERSION = read_version(ROOT)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
cam: CameraService | None = None
settings: dict[str, Any] = {}
models = ModelService(ROOT)


@app.context_processor
def inject_version() -> dict[str, Any]:
    return {
        "app_version": APP_VERSION,
        "app_version_label": version_label(APP_VERSION),
    }


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


def save_settings(data: dict[str, Any]) -> None:
    RUNTIME_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME_CONFIG.write_text(yaml.safe_dump(data, sort_keys=False))


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


@app.route("/")
def index():
    status = require_cam().status()
    return render_template(
        "index.html",
        status=status,
        kit_id=settings.get("kit_id", "??"),
        disk=disk_info("/mnt/recordings"),
    )


@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    c = require_cam()
    message = None
    error = None
    if request.method == "POST":
        try:
            if request.form.get("action") == "reset":
                defaults = recording_default_values()
                apply_recording_settings(
                    c,
                    defaults["width"],
                    defaults["height"],
                    defaults["fps"],
                    defaults["save_dir"],
                )
                message = "Settings reset to defaults."
            else:
                apply_recording_settings(
                    c,
                    int(request.form["width"]),
                    int(request.form["height"]),
                    int(request.form["fps"]),
                    request.form.get("save_dir", settings.get("save_dir")).strip(),
                )
                message = "Settings saved."
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
    status = c.status()
    return render_template(
        "settings.html",
        status=status,
        resolutions=RESOLUTIONS,
        fps_choices=FPS_CHOICES,
        settings=settings,
        defaults=recording_default_values(),
        controls=c.list_v4l_controls(),
        preset_names=list(load_presets().keys()),
        message=message,
        error=error,
        kit_id=settings.get("kit_id", "??"),
    )


@app.route("/settings/control", methods=["POST"])
def settings_control():
    name = request.form.get("name", "")
    value = request.form.get("value", "")
    try:
        require_cam().set_v4l_control(name, value)
        return redirect(url_for("settings_page"))
    except Exception as exc:  # noqa: BLE001
        return render_template(
            "settings.html",
            status=require_cam().status(),
            resolutions=RESOLUTIONS,
            fps_choices=FPS_CHOICES,
            settings=settings,
            defaults=recording_default_values(),
            controls=require_cam().list_v4l_controls(),
            preset_names=list(load_presets().keys()),
            message=None,
            error=str(exc),
            kit_id=settings.get("kit_id", "??"),
        )


@app.route("/api/settings/presets", methods=["GET", "POST"])
def api_settings_presets():
    c = require_cam()
    if request.method == "GET":
        return jsonify({"ok": True, "names": list(load_presets().keys())})
    data = request.get_json(silent=True) or {}
    action = str(data.get("action") or "")
    try:
        name = clean_preset_name(str(data.get("name") or ""))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    presets = load_presets()
    if action == "save":
        presets[name] = capture_preset(c)
        write_presets(presets)
        return jsonify({"ok": True, "names": list(presets.keys()), "name": name})
    if action == "delete":
        if name not in presets:
            return jsonify({"ok": False, "error": "That saved setup was not found."}), 404
        presets.pop(name, None)
        write_presets(presets)
        return jsonify({"ok": True, "names": list(presets.keys())})
    if action == "load":
        preset = presets.get(name)
        if not isinstance(preset, dict):
            return jsonify({"ok": False, "error": "That saved setup was not found."}), 404
        try:
            apply_preset(c, preset)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify(
            {
                "ok": True,
                "name": name,
                "names": list(presets.keys()),
                "status": c.status(),
                "controls": c.list_v4l_controls(),
            }
        )
    return jsonify({"ok": False, "error": "Unknown action."}), 400


@app.route("/api/v4l/controls", methods=["GET"])
def api_v4l_controls():
    return jsonify({"ok": True, "controls": require_cam().list_v4l_controls()})


@app.route("/api/v4l/control", methods=["POST"])
def api_v4l_control():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or request.form.get("name", ""))
    value = data.get("value", request.form.get("value", ""))
    try:
        require_cam().set_v4l_control(name, value)
        return jsonify({"ok": True, "controls": require_cam().list_v4l_controls()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/admin")
def admin_page():
    svc_code, svc_out = run_cmd(["systemctl", "is-active", SERVICE_NAME])
    journal_code, journal = run_cmd(
        ["journalctl", "-u", SERVICE_NAME, "-n", "40", "--no-pager"]
    )
    return render_template(
        "admin.html",
        kit_id=settings.get("kit_id", "??"),
        status=require_cam().status(),
        disk=disk_info("/mnt/recordings"),
        network=network_snapshot(),
        service_active=(svc_out.strip() == "active"),
        service_raw=svc_out,
        journal=journal if journal_code == 0 else journal,
        videos=list_recent_videos(),
    )


@app.route("/admin/service", methods=["POST"])
def admin_service():
    action = request.form.get("action", "")
    if action not in {"start", "stop", "restart", "status"}:
        return jsonify({"ok": False, "error": "bad action"}), 400
    # status needs no root; start/stop/restart use systemd --user? No, system service needs sudoers.
    if action == "status":
        code, out = run_cmd(["systemctl", "status", SERVICE_NAME, "--no-pager"])
        return jsonify({"ok": True, "code": code, "output": out})
    code, out = run_cmd(["sudo", "-n", "systemctl", action, SERVICE_NAME])
    if code != 0:
        # Fallback message for missing passwordless sudo
        return jsonify(
            {
                "ok": False,
                "error": "Need passwordless sudo for systemctl (setup script installs this).",
                "output": out,
            }
        ), 500
    return jsonify({"ok": True, "output": out or f"{action} requested"})


@app.route("/api/status")
def api_status():
    return jsonify(
        {
            "camera": require_cam().status(),
            "disk": disk_info("/mnt/recordings"),
            "kit_id": settings.get("kit_id"),
            "version": APP_VERSION,
            "version_label": version_label(APP_VERSION),
        }
    )


@app.route("/api/update", methods=["GET"])
def api_update():
    force = str(request.args.get("force", "")).lower() in {"1", "true", "yes"}
    return jsonify(check_for_update(ROOT, force=force))


@app.route("/api/update/apply", methods=["POST"])
def api_update_apply():
    try:
        if require_cam().status().get("recording"):
            return jsonify(
                {
                    "ok": False,
                    "error": "Stop recording before updating the kit software.",
                }
            ), 409
    except Exception:  # noqa: BLE001
        pass

    release = check_for_update(ROOT, force=True)
    if not release.get("update_available"):
        return jsonify(
            {
                "ok": False,
                "error": release.get("error") or "No newer release is available.",
                "current_version": release.get("current_version"),
            }
        ), 400

    ok, message = start_apply_update(ROOT, release)
    if not ok:
        return jsonify({"ok": False, "error": message}), 500
    return jsonify(
        {
            "ok": True,
            "message": message,
            "target_version": release.get("latest_version"),
        }
    )


@app.route("/api/record/start", methods=["POST"])
def api_record_start():
    try:
        path = require_cam().start_recording()
        return jsonify({"ok": True, "file": path})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/record/stop", methods=["POST"])
def api_record_stop():
    info = require_cam().stop_recording()
    converting = None
    final_file = info.get("file")
    if (
        settings.get("finalize_to_mp4", True)
        and info.get("was_recording")
        and info.get("file")
    ):
        src = Path(str(info["file"]))
        if src.suffix.lower() == ".avi" and src.exists():
            converting = queue_avi_to_mp4(
                src,
                crf=int(settings.get("h264_crf", 20)),
                preset=str(settings.get("h264_preset", "veryfast")),
                delete_source=bool(settings.get("delete_avi_after_convert", True)),
            )
            final_file = converting.get("dest") or final_file
    return jsonify(
        {
            "ok": True,
            **info,
            "file": final_file,
            "converting": converting,
            "message": (
                converting.get("message")
                if converting
                else "Recording saved."
            ),
        }
    )


def current_save_dir() -> Path:
    return Path(settings.get("save_dir", "/mnt/recordings/videos"))


def apply_save_dir(path: str) -> Path:
    resolved = ensure_allowed_dir(path)
    require_cam().reconfigure(save_dir=str(resolved))
    settings["save_dir"] = str(resolved)
    save_settings(settings)
    return resolved


@app.route("/videos")
def videos_page():
    folder = current_save_dir()
    return render_template(
        "videos.html",
        kit_id=settings.get("kit_id", "??"),
        save_dir=str(folder),
        videos=list_videos(folder),
        volumes=list_volumes(),
        disk=disk_info(str(folder if folder.exists() else "/mnt/recordings")),
    )


@app.route("/api/videos", methods=["GET"])
def api_videos():
    folder = current_save_dir()
    return jsonify({"ok": True, "save_dir": str(folder), "videos": list_videos(folder)})


@app.route("/api/videos/rename", methods=["POST"])
def api_videos_rename():
    data = request.get_json(silent=True) or {}
    old_name = str(data.get("old_name") or "")
    new_name = str(data.get("new_name") or "")
    try:
        folder = current_save_dir()
        src = video_path_in_folder(folder, old_name)
        dst = video_path_in_folder(folder, new_name)
        if not src.exists():
            raise FileNotFoundError("Video not found.")
        if dst.exists() and dst != src:
            raise FileExistsError("A file with that name already exists.")
        src.rename(dst)
        delete_preview_for(src)
        return jsonify({"ok": True, "videos": list_videos(folder)})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/videos/delete", methods=["POST"])
def api_videos_delete():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "")
    try:
        folder = current_save_dir()
        path = video_path_in_folder(folder, name)
        if not path.exists():
            raise FileNotFoundError("Video not found.")
        delete_preview_for(path)
        path.unlink()
        return jsonify({"ok": True, "videos": list_videos(folder)})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/videos/watch/<path:name>")
def videos_watch(name: str):
    try:
        folder = current_save_dir()
        path = video_path_in_folder(folder, name)
        if path.suffix.lower() == ".mp4":
            job = read_job(path)
            if job and job.get("status") == "converting":
                return jsonify(
                    {
                        "ok": False,
                        "error": "Still converting to MP4 — try again in a moment.",
                        "converting": True,
                    }
                ), 409
        if not path.exists():
            return jsonify({"ok": False, "error": "Video not found."}), 404
        # Prefer finalized H.264 MP4; legacy AVI still gets an on-demand preview.
        playable = ensure_browser_playable(path)
        mime, _ = mimetypes.guess_type(str(playable))
        return send_file(
            playable,
            mimetype=mime or "video/mp4",
            conditional=True,
            download_name=playable.name,
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/videos/download/<path:name>")
def videos_download(name: str):
    try:
        path = video_path_in_folder(current_save_dir(), name)
        if path.suffix.lower() == ".mp4":
            job = read_job(path)
            if job and job.get("status") == "converting":
                return jsonify(
                    {
                        "ok": False,
                        "error": "Still converting — download will be available when ready.",
                        "converting": True,
                    }
                ), 409
        if not path.exists():
            return jsonify({"ok": False, "error": "Video not found."}), 404
        mime, _ = mimetypes.guess_type(str(path))
        return send_file(
            path,
            mimetype=mime or "application/octet-stream",
            as_attachment=True,
            download_name=path.name,
            conditional=True,
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/storage", methods=["GET"])
def api_storage():
    return jsonify(
        {
            "ok": True,
            "save_dir": str(current_save_dir()),
            "volumes": list_volumes(),
        }
    )


@app.route("/api/browse", methods=["GET"])
def api_browse():
    path = request.args.get("path") or str(current_save_dir())
    try:
        listing = list_directory(path)
        return jsonify({"ok": True, **listing, "volumes": list_volumes()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/save-dir", methods=["POST"])
def api_save_dir():
    data = request.get_json(silent=True) or {}
    path = str(data.get("path") or "").strip()
    try:
        resolved = apply_save_dir(path)
        analysis = analyze_storage_for_recording(
            resolved,
            width=int(settings.get("width", 1280)),
            height=int(settings.get("height", 720)),
            fps=int(settings.get("fps", 60)),
        )
        return jsonify(
            {
                "ok": True,
                "save_dir": str(resolved),
                "videos": list_videos(resolved),
                "volumes": list_volumes(),
                "analysis": analysis,
            }
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/storage/analyze", methods=["GET", "POST"])
def api_storage_analyze():
    data = request.get_json(silent=True) or {}
    path = (
        str(data.get("path") or request.args.get("path") or current_save_dir()).strip()
    )
    width = int(data.get("width") or request.args.get("width") or settings.get("width", 1280))
    height = int(
        data.get("height") or request.args.get("height") or settings.get("height", 720)
    )
    fps = int(data.get("fps") or request.args.get("fps") or settings.get("fps", 60))
    run_benchmark = str(
        data.get("benchmark", request.args.get("benchmark", "1"))
    ).lower() not in {"0", "false", "no"}
    try:
        analysis = analyze_storage_for_recording(
            path,
            width=width,
            height=height,
            fps=fps,
            run_benchmark=run_benchmark,
        )
        return jsonify({"ok": True, "analysis": analysis})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.errorhandler(413)
def request_too_large(_err: Exception):
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "That file is too large (limit 800 MB)."}), 413
    return "That file is too large (limit 800 MB).", 413


@app.route("/model")
def model_page():
    return render_template(
        "model.html",
        kit_id=settings.get("kit_id", "??"),
        model=models.status(),
    )


@app.route("/deploy")
def deploy_page():
    return render_template(
        "deploy.html",
        kit_id=settings.get("kit_id", "??"),
        model=models.status(),
    )


@app.route("/api/model", methods=["GET"])
def api_model():
    return jsonify({"ok": True, **models.status()})


@app.route("/api/model/upload", methods=["POST"])
def api_model_upload():
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify({"ok": False, "error": "Choose a weights file."}), 400
    try:
        status = models.install_upload(
            upload.read(),
            upload.filename,
            request.form.get("class_names", ""),
        )
        return jsonify({"ok": True, **status})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/model/names", methods=["POST"])
def api_model_names():
    data = request.get_json(silent=True) or {}
    try:
        status = models.update_names(str(data.get("class_names") or ""))
        return jsonify({"ok": True, **status})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/model/metrics", methods=["GET"])
def api_model_metrics_get():
    return jsonify({"ok": True, "metrics": models.list_metrics()})


@app.route("/api/model/metrics", methods=["PUT"])
def api_model_metrics_put():
    data = request.get_json(silent=True)
    payload = data.get("metrics") if isinstance(data, dict) else data
    try:
        metrics = models.save_metrics(payload)
        return jsonify({"ok": True, "metrics": metrics})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/model/run", methods=["POST"])
def api_model_run():
    data = request.get_json(silent=True) or {}
    try:
        threshold = float(data.get("threshold", 0.5))
    except (TypeError, ValueError):
        threshold = 0.5
    speed = str(data.get("speed") or "fast")
    try:
        jpeg = require_cam().get_jpeg(quality=90)
        if jpeg is None:
            return jsonify({"ok": False, "error": "The camera does not have a frame yet."}), 503
        return jsonify(models.predict_jpeg(jpeg, threshold, speed=speed))
    except ModelBusy as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/video_feed")
def video_feed():
    def generate():
        while True:
            try:
                jpeg = require_cam().get_jpeg()
            except Exception:  # noqa: BLE001
                jpeg = None
            if jpeg is None:
                time.sleep(0.05)
                continue
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
            )
            time.sleep(0.03)

    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")


def list_recent_videos(limit: int = 20) -> list[dict[str, Any]]:
    return list_videos(current_save_dir(), limit=limit)


def create_app() -> Flask:
    init_camera()
    return app


def main() -> None:
    init_camera()
    host = settings.get("host", "0.0.0.0")
    port = int(settings.get("port", 8080))
    # threaded=True so MJPEG stream + API work together
    app.run(host=host, port=port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
