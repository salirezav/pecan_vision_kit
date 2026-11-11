"""Admin, status, update, and activity routes."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from update import check_for_update, start_apply_update, version_label
import runtime as rt

bp = Blueprint("system", __name__)


@bp.route("/admin/service", methods=["POST"])
def admin_service():
    action = request.form.get("action", "")
    if action not in {"start", "stop", "restart", "status"}:
        return jsonify({"ok": False, "error": "bad action"}), 400
    # status needs no root; start/stop/restart use systemd --user? No, system service needs sudoers.
    if action == "status":
        code, out = rt.run_cmd(["systemctl", "status", rt.SERVICE_NAME, "--no-pager"])
        return jsonify({"ok": True, "code": code, "output": out})
    code, out = rt.run_cmd(["sudo", "-n", "systemctl", action, rt.SERVICE_NAME])
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

@bp.route("/api/status")
def api_status():
    return jsonify(
        {
            "camera": rt.require_cam().status(),
            "disk": rt.disk_info("/mnt/recordings"),
            "kit_id": rt.settings.get("kit_id"),
            "version": rt.APP_VERSION,
            "version_label": version_label(rt.APP_VERSION),
        }
    )

@bp.route("/api/update", methods=["GET"])
def api_update():
    force = str(request.args.get("force", "")).lower() in {"1", "true", "yes"}
    return jsonify(check_for_update(rt.ROOT, force=force))

@bp.route("/api/update/apply", methods=["POST"])
def api_update_apply():
    try:
        if rt.require_cam().status().get("recording"):
            return jsonify(
                {
                    "ok": False,
                    "error": "Stop recording before updating the kit software.",
                }
            ), 409
    except Exception:  # noqa: BLE001
        pass

    release = check_for_update(rt.ROOT, force=True)
    if not release.get("update_available"):
        return jsonify(
            {
                "ok": False,
                "error": release.get("error") or "No newer release is available.",
                "current_version": release.get("current_version"),
            }
        ), 400

    ok, message = start_apply_update(rt.ROOT, release)
    if not ok:
        return jsonify({"ok": False, "error": message}), 500
    return jsonify(
        {
            "ok": True,
            "message": message,
            "target_version": release.get("latest_version"),
        }
    )

@bp.route("/api/activity")
def api_activity():
    recording = False
    try:
        recording = bool(rt.require_cam().status().get("recording"))
    except Exception:  # noqa: BLE001
        recording = False
    return jsonify(
        {
            "ok": True,
            "recording": recording,
            "deploy_live": bool(rt.deploy_live.status().get("running")),
        }
    )

