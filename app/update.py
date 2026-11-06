"""Check GitHub Releases and launch the on-kit updater."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

GITHUB_REPO = "salirezav/pecan_vision_kit"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
CACHE_TTL_SEC = 3600
UPDATE_STATUS_PATH = Path("/mnt/recordings/update-status.json")
CACHE_PATH = Path("/mnt/recordings/update-check-cache.json")
USER_AGENT = "pecan-vision-kit-updater"


def read_version(root: Path) -> str:
    path = root / "VERSION"
    if not path.exists():
        return "0.0.0"
    return path.read_text(encoding="utf-8").strip() or "0.0.0"


def version_label(version: str) -> str:
    """Human label: 0.x builds are previews until 1.0.0."""
    parts = _parse_semver(version)
    if parts is None:
        return version
    major, _minor, _patch = parts
    if major == 0:
        return f"{version} preview"
    return version


def _parse_semver(value: str) -> tuple[int, int, int] | None:
    cleaned = value.strip().lstrip("vV")
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", cleaned)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def is_newer(remote: str, local: str) -> bool:
    remote_parts = _parse_semver(remote)
    local_parts = _parse_semver(local)
    if remote_parts is None or local_parts is None:
        return remote.lstrip("vV") != local.lstrip("vV")
    return remote_parts > local_parts


def read_update_status() -> dict[str, Any] | None:
    if not UPDATE_STATUS_PATH.exists():
        return None
    try:
        data = json.loads(UPDATE_STATUS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def write_update_status(data: dict[str, Any]) -> None:
    UPDATE_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    UPDATE_STATUS_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _load_cache() -> dict[str, Any] | None:
    if not CACHE_PATH.exists():
        return None
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        checked_at = float(data.get("checked_at", 0))
        if time.time() - checked_at > CACHE_TTL_SEC:
            return None
        return data
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _save_cache(payload: dict[str, Any]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _http_get_json(url: str, timeout: float = 12.0) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Unexpected GitHub API response")
    return data


def _pick_assets(release: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """Return (tarball_url, sha256_url, sha256_hex) from release assets."""
    assets = release.get("assets") or []
    tarball_url = None
    sha_url = None
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        url = asset.get("browser_download_url")
        if not url:
            continue
        lower = name.lower()
        if lower.endswith(".tar.gz") and "pecan" in lower:
            tarball_url = str(url)
        elif lower == "sha256sums" or lower.endswith(".sha256") or lower == "sha256sums.txt":
            sha_url = str(url)
    return tarball_url, sha_url, None


def check_for_update(root: Path, *, force: bool = False) -> dict[str, Any]:
    local = read_version(root)
    result: dict[str, Any] = {
        "ok": True,
        "current_version": local,
        "current_label": version_label(local),
        "update_available": False,
        "latest_version": None,
        "latest_label": None,
        "release_notes": None,
        "release_url": None,
        "tarball_url": None,
        "sha256_url": None,
        "checked_at": time.time(),
        "error": None,
        "cached": False,
        "last_status": read_update_status(),
    }

    if not force:
        cached = _load_cache()
        if cached is not None:
            cached["current_version"] = local
            cached["current_label"] = version_label(local)
            cached["last_status"] = result["last_status"]
            cached["cached"] = True
            # Recompute availability against current local version.
            latest = cached.get("latest_version")
            if latest:
                cached["update_available"] = is_newer(str(latest), local)
            return cached

    try:
        release = _http_get_json(GITHUB_API)
        tag = str(release.get("tag_name") or "").strip()
        if not tag:
            raise ValueError("Release has no tag_name")
        tarball_url, sha_url, _ = _pick_assets(release)
        # Prefer attached release asset; fall back to source archive URL.
        if not tarball_url:
            tarball_url = (
                f"https://github.com/{GITHUB_REPO}/releases/download/{tag}/"
                f"pecan_vision_kit-{tag.lstrip('v')}.tar.gz"
            )
        notes = release.get("body") or ""
        result.update(
            {
                "latest_version": tag.lstrip("vV"),
                "latest_label": version_label(tag.lstrip("vV")),
                "release_notes": str(notes).strip()[:4000],
                "release_url": release.get("html_url"),
                "tarball_url": tarball_url,
                "sha256_url": sha_url,
                "update_available": is_newer(tag, local),
            }
        )
        _save_cache({k: v for k, v in result.items() if k != "last_status"})
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        result["ok"] = False
        result["error"] = str(exc)
        # Soft-fail: keep current version, no banner.
        result["update_available"] = False

    return result


def start_apply_update(root: Path, release: dict[str, Any]) -> tuple[bool, str]:
    """Launch apply-update.sh detached via passwordless sudo."""
    script = root / "scripts" / "apply-update.sh"
    if not script.is_file():
        return False, f"Missing updater script: {script}"

    version = str(release.get("latest_version") or "").strip()
    tarball = str(release.get("tarball_url") or "").strip()
    sha_url = str(release.get("sha256_url") or "").strip()
    if not version or not tarball:
        return False, "Release is missing version or download URL."

    log_path = Path("/mnt/recordings/update-apply.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    write_update_status(
        {
            "state": "starting",
            "target_version": version,
            "started_at": time.time(),
            "message": "Update requested from the dashboard.",
        }
    )

    env = os.environ.copy()
    env["PECAN_UPDATE_VERSION"] = version
    env["PECAN_UPDATE_TARBALL_URL"] = tarball
    env["PECAN_UPDATE_SHA256_URL"] = sha_url
    env["PECAN_KIT_ROOT"] = str(root)

    # Absolute paths must match config/sudoers-pecan-vision.
    # setsid + start_new_session so stopping pecan-vision does not kill the installer.
    cmd = [
        "sudo",
        "-n",
        "/usr/bin/setsid",
        "/usr/bin/bash",
        str(script),
    ]
    try:
        with open(log_path, "a", encoding="utf-8") as logf:
            logf.write(f"\n--- apply {version} at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            subprocess.Popen(
                cmd,
                stdout=logf,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
                start_new_session=True,
            )
    except OSError as exc:
        return False, str(exc)
    return True, "Update started. The kit will restart the web app shortly."
