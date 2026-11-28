"""Model check and Deploy live inference routes."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

import deploy_runs
import roi_store
import runtime as rt
from segment import ModelBusy

bp = Blueprint("model", __name__)


def _camera_size() -> tuple[int | None, int | None]:
    try:
        status = rt.require_cam().status()
    except Exception:  # noqa: BLE001
        return None, None
    width = int(status.get("width") or 0) or None
    height = int(status.get("height") or 0) or None
    return width, height


def _roi_for_predict(data: dict) -> dict | None:
    """Body roi wins; otherwise the shared camera ROI. None means full frame."""
    if "roi" in data:
        raw = data.get("roi")
        if raw is None or raw is False or raw == {}:
            return None
        return roi_store.validate_roi(raw)
    return roi_store.load_roi()


def _roi_space() -> dict | None:
    width, height = _camera_size()
    if width and height:
        return {"width": width, "height": height}
    return None


@bp.app_errorhandler(413)
def request_too_large(_err: Exception):
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "That file is too large (limit 800 MB)."}), 413
    return "That file is too large (limit 800 MB).", 413


@bp.route("/api/model", methods=["GET"])
def api_model():
    return jsonify({"ok": True, **rt.models.status()})


@bp.route("/api/model/bundled", methods=["GET"])
def api_model_bundled():
    return jsonify(
        {
            "ok": True,
            "models": rt.models.bundled_pending(),
            "library_path": str(rt.models.library_dir()),
        }
    )


@bp.route("/api/model/install-bundled", methods=["POST"])
def api_model_install_bundled():
    data = request.get_json(silent=True) or {}
    raw = data.get("files")
    files = raw if isinstance(raw, list) else None
    try:
        summary = rt.models.install_bundled(files)
    except ModelBusy as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400
    if summary["bundled_errors"] and not summary["bundled_installed"]:
        return jsonify(
            {
                "ok": False,
                "error": summary["bundled_errors"][0]["error"],
                **summary,
            }
        ), 400
    return jsonify({"ok": True, **summary, **rt.models.status()})


@bp.route("/api/model/dismiss-bundled", methods=["POST"])
def api_model_dismiss_bundled():
    data = request.get_json(silent=True) or {}
    raw = data.get("files")
    files = raw if isinstance(raw, list) else None
    try:
        result = rt.models.dismiss_bundled(files)
        return jsonify({"ok": True, **result})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


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
    except ModelBusy as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.route("/api/model/names", methods=["POST"])
def api_model_names():
    data = request.get_json(silent=True) or {}
    try:
        status = rt.models.update_names(str(data.get("class_names") or ""))
        return jsonify({"ok": True, **status})
    except ModelBusy as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.route("/api/model/names/reset", methods=["POST"])
def api_model_names_reset():
    try:
        status = rt.models.reset_names()
        return jsonify({"ok": True, **status})
    except ModelBusy as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.route("/api/model/delete", methods=["POST"])
def api_model_delete():
    data = request.get_json(silent=True) or {}
    model_id = str(data.get("id") or "")
    try:
        if rt.review_video.is_running():
            return jsonify(
                {"ok": False, "error": "Finish the video review before deleting a model."}
            ), 409
        if rt.deploy_live.status().get("running") and Path(model_id).name == rt.models.weights_path().name:
            return jsonify(
                {"ok": False, "error": "Stop Deploy before deleting the model it is using."}
            ), 409
        status = rt.models.delete_model(model_id)
        return jsonify({"ok": True, **status})
    except ModelBusy as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.route("/api/model/select", methods=["POST"])
def api_model_select():
    data = request.get_json(silent=True) or {}
    try:
        if rt.review_video.is_running():
            return jsonify(
                {"ok": False, "error": "Finish the video review before changing models."}
            ), 409
        if rt.deploy_live.is_running():
            return jsonify(
                {"ok": False, "error": "Stop Deploy before changing models."}
            ), 409
        status = rt.models.select_model(str(data.get("id") or ""))
        return jsonify({"ok": True, **status})
    except ModelBusy as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.route("/api/model/metrics", methods=["GET"])
def api_model_metrics_get():
    return jsonify({"ok": True, **rt.models.metrics_payload()})


@bp.route("/api/model/metrics", methods=["PUT"])
def api_model_metrics_put():
    data = request.get_json(silent=True)
    try:
        if isinstance(data, dict) and ("metrics" in data or "builtin_enabled" in data):
            saved = rt.models.save_metrics(
                data.get("metrics") if "metrics" in data else rt.models.list_metrics(),
                builtin_enabled=data.get("builtin_enabled"),
            )
        else:
            saved = rt.models.save_metrics(data)
        return jsonify({"ok": True, **saved})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.route("/api/model/calibration", methods=["GET"])
def api_model_calibration_get():
    try:
        return jsonify({"ok": True, "calibration": rt.models.get_calibration()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.route("/api/model/calibration", methods=["PUT"])
def api_model_calibration_put():
    data = request.get_json(silent=True) or {}
    payload = data.get("calibration") if isinstance(data, dict) and "calibration" in data else data
    try:
        calibration = rt.models.save_calibration(payload)
        # Live Deploy runs pick up class confidence on the next sample; mass
        # coefficients also recompute stored half-yield share without a new pass.
        try:
            rt.deploy_live.refresh_calibration()
        except Exception:  # noqa: BLE001
            pass
        # Review treats a calibration change as a new report segment.
        try:
            rt.review_video.refresh_calibration()
        except Exception:  # noqa: BLE001
            pass
        return jsonify({"ok": True, "calibration": calibration})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.route("/api/model/roi", methods=["GET"])
def api_model_roi_get():
    width, height = _camera_size()
    return jsonify({"ok": True, **roi_store.status(width=width, height=height)})


@bp.route("/api/model/roi", methods=["PUT"])
def api_model_roi_put():
    data = request.get_json(silent=True) or {}
    raw = data.get("roi") if isinstance(data, dict) and "roi" in data else data
    width, height = _camera_size()
    try:
        rect = roi_store.save_roi(raw, width=width, height=height)
        return jsonify({"ok": True, **roi_store.status(width=width, height=height), "roi": rect})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/model/roi", methods=["DELETE"])
def api_model_roi_delete():
    roi_store.clear_roi()
    width, height = _camera_size()
    return jsonify({"ok": True, **roi_store.status(width=width, height=height)})


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
    calibration = data.get("calibration") if isinstance(data.get("calibration"), dict) else None
    class_thresholds = None
    raw_thresholds = data.get("class_thresholds")
    if isinstance(raw_thresholds, dict):
        class_thresholds = {}
        for key, value in raw_thresholds.items():
            try:
                class_thresholds[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
    try:
        roi = _roi_for_predict(data)
        jpeg = rt.capture_infer_jpeg(speed)
        if jpeg is None:
            return jsonify({"ok": False, "error": "The camera does not have a frame yet."}), 503
        return jsonify(
            rt.models.predict_jpeg(
                jpeg,
                threshold,
                speed=speed,
                draw=bool(draw),
                roi=roi,
                roi_space=_roi_space() if roi else None,
                class_thresholds=class_thresholds,
                calibration=calibration,
            )
        )
    except ModelBusy as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
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
        interval_sec = float(data.get("interval_sec", 1.0))
    except (TypeError, ValueError):
        interval_sec = 1.0
    try:
        if rt.review_video.is_running():
            return jsonify(
                {"ok": False, "error": "Stop Review before starting a live Deploy run."}
            ), 409
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
        return jsonify(
            {
                "ok": True,
                **rt.deploy_live.start(threshold, speed, interval_sec=interval_sec),
            }
        )
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


@bp.route("/api/deploy/runs", methods=["GET"])
def api_deploy_runs_list():
    return jsonify({"ok": True, "runs": deploy_runs.list_runs()})


@bp.route("/api/deploy/runs/<run_id>", methods=["GET"])
def api_deploy_runs_get(run_id: str):
    try:
        return jsonify({"ok": True, **deploy_runs.get_run(run_id)})
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "That run was not found."}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/deploy/runs/<run_id>", methods=["DELETE"])
def api_deploy_runs_delete(run_id: str):
    try:
        status = rt.deploy_live.status()
        if status.get("running") and status.get("run_id") == run_id:
            return jsonify({"ok": False, "error": "Stop the live run before deleting it."}), 409
        deploy_runs.delete_run(run_id)
        return jsonify({"ok": True})
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "That run was not found."}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/deploy/runs/<run_id>/csv", methods=["GET"])
def api_deploy_runs_csv(run_id: str):
    try:
        path = deploy_runs.csv_path(run_id)
        return send_file(
            path,
            mimetype="text/csv",
            as_attachment=True,
            download_name=f"deploy-{run_id}.csv",
        )
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "That run was not found."}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
