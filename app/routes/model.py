"""Model check and Deploy live inference routes."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from segment import ModelBusy
import runtime as rt

bp = Blueprint("model", __name__)


@bp.app_errorhandler(413)
def request_too_large(_err: Exception):
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "That file is too large (limit 800 MB)."}), 413
    return "That file is too large (limit 800 MB).", 413

@bp.route("/api/model", methods=["GET"])
def api_model():
    return jsonify({"ok": True, **rt.models.status()})

@bp.route("/api/model/upload", methods=["POST"])
def api_model_upload():
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify({"ok": False, "error": "Choose a weights file."}), 400
    try:
        status = rt.models.install_upload(
            upload.read(),
            upload.filename,
            request.form.get("class_names", ""),
        )
        return jsonify({"ok": True, **status})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400

@bp.route("/api/model/names", methods=["POST"])
def api_model_names():
    data = request.get_json(silent=True) or {}
    try:
        status = rt.models.update_names(str(data.get("class_names") or ""))
        return jsonify({"ok": True, **status})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400

@bp.route("/api/model/metrics", methods=["GET"])
def api_model_metrics_get():
    return jsonify({"ok": True, "metrics": rt.models.list_metrics()})

@bp.route("/api/model/metrics", methods=["PUT"])
def api_model_metrics_put():
    data = request.get_json(silent=True)
    payload = data.get("metrics") if isinstance(data, dict) else data
    try:
        metrics = rt.models.save_metrics(payload)
        return jsonify({"ok": True, "metrics": metrics})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400

@bp.route("/api/model/run", methods=["POST"])
def api_model_run():
    data = request.get_json(silent=True) or {}
    try:
        threshold = float(data.get("threshold", 0.5))
    except (TypeError, ValueError):
        threshold = 0.5
    speed = str(data.get("speed") or "fast")
    draw = data.get("draw", True)
    if isinstance(draw, str):
        draw = draw.strip().lower() not in {"0", "false", "no"}
    try:
        jpeg = rt.capture_infer_jpeg(speed)
        if jpeg is None:
            return jsonify({"ok": False, "error": "The camera does not have a frame yet."}), 503
        return jsonify(
            rt.models.predict_jpeg(jpeg, threshold, speed=speed, draw=bool(draw))
        )
    except ModelBusy as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500

@bp.route("/api/deploy/live", methods=["GET"])
def api_deploy_live_get():
    return jsonify({"ok": True, **rt.deploy_live.status(), "model": rt.models.status()})

@bp.route("/api/deploy/live/start", methods=["POST"])
def api_deploy_live_start():
    data = request.get_json(silent=True) or {}
    try:
        threshold = float(data.get("threshold", 0.5))
    except (TypeError, ValueError):
        threshold = 0.5
    speed = str(data.get("speed") or "fast")
    try:
        status = rt.models.status()
        if not status.get("installed"):
            return jsonify({"ok": False, "error": "Upload a model first."}), 400
        if not status.get("library_ready"):
            return jsonify(
                {
                    "ok": False,
                    "error": status.get("library_error") or "The model library is not ready.",
                }
            ), 400
        return jsonify({"ok": True, **rt.deploy_live.start(threshold, speed)})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500

@bp.route("/api/deploy/live/stop", methods=["POST"])
def api_deploy_live_stop():
    return jsonify({"ok": True, **rt.deploy_live.stop()})

@bp.route("/api/deploy/live/reset", methods=["POST"])
def api_deploy_live_reset():
    try:
        return jsonify({"ok": True, **rt.deploy_live.reset()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400

