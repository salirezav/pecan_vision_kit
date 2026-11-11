"""HTML page routes."""

from __future__ import annotations

from flask import Blueprint, render_template, request

from camera import FPS_CHOICES, RESOLUTIONS
from storage import list_videos, list_volumes
import runtime as rt

bp = Blueprint("ui", __name__)


@bp.route("/")
def index():
    status = rt.require_cam().status()
    return render_template(
        "index.html",
        status=status,
        kit_id=rt.settings.get("kit_id", "??"),
        disk=rt.disk_info("/mnt/recordings"),
    )

@bp.route("/settings", methods=["GET", "POST"])
def settings_page():
    c = rt.require_cam()
    message = None
    error = None
    if request.method == "POST":
        try:
            if request.form.get("action") == "reset":
                defaults = rt.recording_default_values()
                rt.apply_recording_settings(
                    c,
                    defaults["width"],
                    defaults["height"],
                    defaults["fps"],
                    defaults["save_dir"],
                )
                message = "Settings reset to defaults."
            else:
                rt.apply_recording_settings(
                    c,
                    int(request.form["width"]),
                    int(request.form["height"]),
                    int(request.form["fps"]),
                    request.form.get("save_dir", rt.settings.get("save_dir")).strip(),
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
        settings=rt.settings,
        defaults=rt.recording_default_values(),
        controls=c.list_v4l_controls(),
        preset_names=list(rt.load_presets().keys()),
        message=message,
        error=error,
        kit_id=rt.settings.get("kit_id", "??"),
    )

@bp.route("/admin")
def admin_page():
    svc_code, svc_out = rt.run_cmd(["systemctl", "is-active", rt.SERVICE_NAME])
    journal_code, journal = rt.run_cmd(
        ["journalctl", "-u", rt.SERVICE_NAME, "-n", "40", "--no-pager"]
    )
    return render_template(
        "admin.html",
        kit_id=rt.settings.get("kit_id", "??"),
        status=rt.require_cam().status(),
        disk=rt.disk_info("/mnt/recordings"),
        network=rt.network_snapshot(),
        service_active=(svc_out.strip() == "active"),
        service_raw=svc_out,
        journal=journal if journal_code == 0 else journal,
        videos=rt.list_recent_videos(),
    )

@bp.route("/videos")
def videos_page():
    folder = rt.current_save_dir()
    return render_template(
        "videos.html",
        kit_id=rt.settings.get("kit_id", "??"),
        save_dir=str(folder),
        videos=list_videos(folder),
        volumes=list_volumes(),
        disk=rt.disk_info(str(folder if folder.exists() else "/mnt/recordings")),
    )

@bp.route("/model")
def model_page():
    return render_template(
        "model.html",
        kit_id=rt.settings.get("kit_id", "??"),
        model=rt.models.status(),
    )

@bp.route("/deploy")
def deploy_page():
    return render_template(
        "deploy.html",
        kit_id=rt.settings.get("kit_id", "??"),
        model=rt.models.status(),
    )

