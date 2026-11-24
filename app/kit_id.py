"""Kit number taken from a usda-vision-## hostname."""

from __future__ import annotations

import os
import re
import socket
from typing import Any

# usda-vision-11 or usda-vision-11.local → 11. A third digit does not match.
_KIT_HOSTNAME_RE = re.compile(r"^usda-vision-(\d{1,2})(?:\..*)?$", re.IGNORECASE)


def kit_id_from_hostname(hostname: str | None = None) -> str | None:
    """Two-digit kit number from a usda-vision-## hostname, or None."""
    name = socket.gethostname() if hostname is None else hostname
    match = _KIT_HOSTNAME_RE.match(name.strip())
    if not match:
        return None
    return f"{int(match.group(1)):02d}"


def stored_kit_id(value: Any) -> str:
    text = str(value).strip() if value is not None else ""
    if text.isdigit() and len(text) <= 2:
        return f"{int(text):02d}"
    return text


def apply_hostname_kit_id(data: dict[str, Any], hostname: str | None = None) -> str | None:
    """Set kit_id from the hostname.

    Returns the id when the caller should write it to persistent config.
    Returns None when the hostname does not name a kit, or the saved id
    already matches.
    """
    detected = kit_id_from_hostname(hostname)
    if not detected:
        return None
    os.environ["KIT_ID"] = detected
    changed = stored_kit_id(data.get("kit_id")) != detected
    data["kit_id"] = detected
    return detected if changed else None
