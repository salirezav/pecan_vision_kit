"""Video review page and API routes."""

from __future__ import annotations

from flask import Blueprint, jsonify, request, send_file

import review_runs
import runtime as rt
from review_video import list_review_videos
from segment import ModelBusy

bp = Blueprint("review", __name__)


@bp.route("/api/review", methods=["GET"])
def api_review_get():
    return jsonify(
        {
            "ok": True,
            **rt.review_video.status(),
            "model": rt.models.status(),
            "videos": list_review_videos(rt.current_save_dir()),
            "deploy_live": rt.deploy_live.is_running(),
        }
    )


@bp.route("/api/review/still", methods=["POST"])
def api_review_still():
    data = request.get_json(silent=True) or {}
    name = str(data.get("video") or data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Choose a video."}), 400
    try:
        return jsonify({"ok": True, **rt.review_video.still(name)})
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "That video was not found."}), 404
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.route("/api/review/start", methods=["POST"])
def api_review_start():
    data = request.get_json(silent=True) or {}
    name = str(data.get("video") or data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Choose a video."}), 400
    try:
        frames_per_sec = float(data.get("frames_per_sec", 1.0))
    except (TypeError, ValueError):
        frames_per_sec = 1.0
    try:
        window_sec = float(data.get("window_sec", 60.0))
    except (TypeError, ValueError):
        window_sec = 60.0
    roi = data.get("roi")
    try:
        if rt.deploy_live.is_running():
            return jsonify(
                {"ok": False, "error": "Stop Deploy before starting a video review."}
            ), 409
        status = rt.models.status()
        if not status.get("installed"):
            return jsonify({"ok": False, "error": "Upload or select a model first."}), 400
        return jsonify(
            {
                "ok": True,
                **rt.review_video.start(
                    video_name=name,
                    frames_per_sec=frames_per_sec,
                    window_sec=window_sec,
                    roi=roi,
                ),
                "model": status,
            }
        )
    except ModelBusy as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "That video was not found."}), 404
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.route("/api/review/action", methods=["POST"])
def api_review_action():
    data = request.get_json(silent=True) or {}
    try:
        return jsonify({"ok": True, **rt.review_video.action(str(data.get("action") or ""))})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.route("/api/review/finish", methods=["POST"])
def api_review_finish():
    try:
        return jsonify({"ok": True, **rt.review_video.finish()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.route("/api/review/runs", methods=["GET"])
def api_review_runs_list():
    return jsonify({"ok": True, "runs": review_runs.list_runs()})


@bp.route("/api/review/runs/<run_id>", methods=["GET"])
def api_review_runs_get(run_id: str):
    try:
        return jsonify({"ok": True, **review_runs.get_run(run_id)})
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "That run was not found."}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.route("/api/review/runs/<run_id>/csv", methods=["GET"])
def api_review_runs_csv(run_id: str):
    try:
        path = review_runs.csv_path(run_id)
        return send_file(
            path,
            mimetype="text/csv",
            as_attachment=True,
            download_name=f"review-{run_id}.csv",
        )
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "That run was not found."}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
