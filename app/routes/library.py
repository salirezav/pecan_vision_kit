"""Video library and storage routes."""

from __future__ import annotations

import mimetypes

from flask import Blueprint, jsonify, request, send_file

from convert import cancel_preview_jobs, read_job
from review_video import list_review_videos
from storage import (
    IMAGE_EXTENSIONS,
    analyze_storage_for_recording,
    delete_preview_for,
    ensure_browser_playable,
    list_directory,
    list_videos,
    list_volumes,
    save_video_upload,
    video_path_in_folder,
)
import runtime as rt

bp = Blueprint("library", __name__)


@bp.route("/api/videos", methods=["GET"])
def api_videos():
    folder = rt.current_save_dir()
    return jsonify({"ok": True, "save_dir": str(folder), "videos": list_videos(folder)})


@bp.route("/api/videos/upload", methods=["POST"])
def api_videos_upload():
    upload = request.files.get("file") or request.files.get("video")
    if upload is None or not upload.filename:
        return jsonify({"ok": False, "error": "Choose a video file to upload."}), 400
    try:
        if rt.review_video.is_running():
            return jsonify(
                {"ok": False, "error": "Finish the video review before uploading."}
            ), 409
        folder = rt.current_save_dir()
        path = save_video_upload(folder, upload, upload.filename)
        return jsonify(
            {
                "ok": True,
                "name": path.name,
                "path": str(path),
                "save_dir": str(folder),
                "videos": list_videos(folder),
                "review_videos": list_review_videos(folder),
            }
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400

@bp.route("/api/videos/rename", methods=["POST"])
def api_videos_rename():
    data = request.get_json(silent=True) or {}
    old_name = str(data.get("old_name") or "")
    new_name = str(data.get("new_name") or "")
    try:
        folder = rt.current_save_dir()
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

@bp.route("/api/videos/delete", methods=["POST"])
def api_videos_delete():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "")
    try:
        folder = rt.current_save_dir()
        path = video_path_in_folder(folder, name)
        if not path.exists():
            raise FileNotFoundError("Video not found.")
        delete_preview_for(path)
        path.unlink()
        return jsonify({"ok": True, "videos": list_videos(folder)})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400

@bp.route("/videos/watch/<path:name>")
def videos_watch(name: str):
    try:
        folder = rt.current_save_dir()
        path = video_path_in_folder(folder, name)
        if not path.exists():
            return jsonify({"ok": False, "error": "File not found."}), 404
        suffix = path.suffix.lower()
        if suffix in IMAGE_EXTENSIONS:
            mime, _ = mimetypes.guess_type(str(path))
            return send_file(
                path,
                mimetype=mime or "image/jpeg",
                conditional=True,
                download_name=path.name,
            )
        if suffix == ".mp4":
            job = read_job(path)
            if job and job.get("status") == "converting":
                return jsonify(
                    {
                        "ok": False,
                        "error": "Still converting to MP4 — try again in a moment.",
                        "converting": True,
                    }
                ), 409
        # Prefer finalized H.264 MP4; legacy AVI still gets a queued preview.
        playable = ensure_browser_playable(path)
        if isinstance(playable, dict):
            return jsonify(
                {
                    "ok": False,
                    "error": playable.get("message")
                    or "Still preparing a playable preview — try again in a moment.",
                    "converting": True,
                }
            ), 409
        mime, _ = mimetypes.guess_type(str(playable))
        return send_file(
            playable,
            mimetype=mime or "video/mp4",
            conditional=True,
            download_name=playable.name,
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400

@bp.route("/api/videos/preview/cancel", methods=["POST"])
def api_videos_preview_cancel():
    cancelled = cancel_preview_jobs()
    return jsonify({"ok": True, "cancelled": cancelled})

@bp.route("/videos/download/<path:name>")
def videos_download(name: str):
    try:
        path = video_path_in_folder(rt.current_save_dir(), name)
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

@bp.route("/api/storage", methods=["GET"])
def api_storage():
    return jsonify(
        {
            "ok": True,
            "save_dir": str(rt.current_save_dir()),
            "volumes": list_volumes(),
        }
    )

@bp.route("/api/browse", methods=["GET"])
def api_browse():
    path = request.args.get("path") or str(rt.current_save_dir())
    try:
        listing = list_directory(path)
        return jsonify({"ok": True, **listing, "volumes": list_volumes()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400

@bp.route("/api/save-dir", methods=["POST"])
def api_save_dir():
    data = request.get_json(silent=True) or {}
    path = str(data.get("path") or "").strip()
    try:
        resolved = rt.apply_save_dir(path)
        analysis = analyze_storage_for_recording(
            resolved,
            width=int(rt.settings.get("width", 1280)),
            height=int(rt.settings.get("height", 720)),
            fps=int(rt.settings.get("fps", 60)),
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

@bp.route("/api/storage/analyze", methods=["GET", "POST"])
def api_storage_analyze():
    data = request.get_json(silent=True) or {}
    path = (
        str(data.get("path") or request.args.get("path") or rt.current_save_dir()).strip()
    )
    width = int(data.get("width") or request.args.get("width") or rt.settings.get("width", 1280))
    height = int(
        data.get("height") or request.args.get("height") or rt.settings.get("height", 720)
    )
    fps = int(data.get("fps") or request.args.get("fps") or rt.settings.get("fps", 60))
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

