"""Recording, live stream, and camera control routes."""

from __future__ import annotations

import time

from flask import Blueprint, Response, jsonify, redirect, render_template, request, url_for

from camera import FPS_CHOICES, RESOLUTIONS
from convert import queue_avi_to_mp4
import runtime as rt

bp = Blueprint("camera", __name__)


@bp.route("/settings/control", methods=["POST"])
def settings_control():
    name = request.form.get("name", "")
    value = request.form.get("value", "")
    try:
        rt.require_cam().set_v4l_control(name, value)
        return redirect(url_for("ui.settings_page"))
    except Exception as exc:  # noqa: BLE001
        return render_template(
            "settings.html",
            status=rt.require_cam().status(),
            resolutions=RESOLUTIONS,
            fps_choices=FPS_CHOICES,
            settings=rt.settings,
            defaults=rt.recording_default_values(),
            controls=rt.require_cam().list_v4l_controls(),
            preset_names=list(rt.load_presets().keys()),
            message=None,
            error=str(exc),
            kit_id=rt.settings.get("kit_id", "??"),
        )

@bp.route("/api/settings/presets", methods=["GET", "POST"])
def api_settings_presets():
    c = rt.require_cam()
    if request.method == "GET":
        return jsonify({"ok": True, "names": list(rt.load_presets().keys())})
    data = request.get_json(silent=True) or {}
    action = str(data.get("action") or "")
    try:
        name = rt.clean_preset_name(str(data.get("name") or ""))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    presets = rt.load_presets()
    if action == "save":
        presets[name] = rt.capture_preset(c)
        rt.write_presets(presets)
        return jsonify({"ok": True, "names": list(presets.keys()), "name": name})
    if action == "delete":
        if name not in presets:
            return jsonify({"ok": False, "error": "That saved setup was not found."}), 404
        presets.pop(name, None)
        rt.write_presets(presets)
        return jsonify({"ok": True, "names": list(presets.keys())})
    if action == "load":
        preset = presets.get(name)
        if not isinstance(preset, dict):
            return jsonify({"ok": False, "error": "That saved setup was not found."}), 404
        try:
            rt.apply_preset(c, preset)
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

@bp.route("/api/v4l/controls", methods=["GET"])
def api_v4l_controls():
    return jsonify({"ok": True, "controls": rt.require_cam().list_v4l_controls()})

@bp.route("/api/v4l/control", methods=["POST"])
def api_v4l_control():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or request.form.get("name", ""))
    value = data.get("value", request.form.get("value", ""))
    try:
        rt.require_cam().set_v4l_control(name, value)
        return jsonify({"ok": True, "controls": rt.require_cam().list_v4l_controls()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400

@bp.route("/api/record/start", methods=["POST"])
def api_record_start():
    try:
        path = rt.require_cam().start_recording()
        return jsonify({"ok": True, "file": path})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500

@bp.route("/api/record/stop", methods=["POST"])
def api_record_stop():
    info = rt.require_cam().stop_recording()
    converting = None
    final_file = info.get("file")
    if (
        rt.settings.get("finalize_to_mp4", True)
        and info.get("was_recording")
        and info.get("file")
    ):
        src = Path(str(info["file"]))
        if src.suffix.lower() == ".avi" and src.exists():
            converting = queue_avi_to_mp4(
                src,
                crf=int(rt.settings.get("h264_crf", 20)),
                preset=str(rt.settings.get("h264_preset", "veryfast")),
                delete_source=bool(rt.settings.get("delete_avi_after_convert", True)),
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

@bp.route("/video_feed")
def video_feed():
    def generate():
        while True:
            try:
                jpeg = rt.require_cam().get_jpeg()
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

