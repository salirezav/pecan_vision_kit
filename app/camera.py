"""Camera capture + recording helpers (UVC / V4L2 via OpenCV)."""

from __future__ import annotations

import glob
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2

_CTRL_HEADER = re.compile(
    r"^\s*([a-z0-9_]+)\s+0x[0-9a-fA-F]+\s+\((\w+)\)\s*:\s*(.*)$"
)
_MENU_ITEM = re.compile(r"^\s*(\d+):\s*(.+)\s*$")
_SKIP_CTRL_TYPES = frozenset({"rect", "bitmask", "button", "string"})


RESOLUTIONS = [
    (1920, 1200),
    (1920, 1080),
    (1600, 1200),
    (1280, 960),
    (1280, 720),
    (640, 480),
    (512, 512),
]

FPS_CHOICES = [10, 30, 60, 80, 120, 150, 200]


def find_camera_device(preferred: str, fallback: str) -> str:
    if preferred and os.path.exists(preferred):
        return preferred
    matches = sorted(
        glob.glob(
            "/dev/v4l/by-id/usb-Global_Shutter_Camera_Global_Shutter_Camera_*-video-index0"
        )
    )
    if matches:
        return matches[0]
    if fallback and os.path.exists(fallback):
        return fallback
    if os.path.exists("/dev/video0"):
        return "/dev/video0"
    raise FileNotFoundError("No Global Shutter / V4L2 camera device found")


@dataclass
class CameraService:
    device: str
    width: int = 1280
    height: int = 720
    fps: int = 60
    save_dir: str = "/mnt/recordings/videos"
    fourcc_name: str = "MJPG"
    record_extension: str = ".avi"

    _cap: cv2.VideoCapture | None = field(default=None, init=False, repr=False)
    _writer: cv2.VideoWriter | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _frame: Any = field(default=None, init=False, repr=False)
    _running: bool = field(default=False, init=False, repr=False)
    _recording: bool = field(default=False, init=False, repr=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _current_file: str | None = field(default=None, init=False, repr=False)
    _frames_written: int = field(default=0, init=False, repr=False)
    _last_error: str | None = field(default=None, init=False, repr=False)
    _opened_device: str | None = field(default=None, init=False, repr=False)

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._open_locked()
            self._running = True
            self._thread = threading.Thread(target=self._loop, name="cam-loop", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._running = False
            self._stop_recording_locked()
            if self._cap is not None:
                self._cap.release()
                self._cap = None
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    def reconfigure(
        self,
        *,
        width: int | None = None,
        height: int | None = None,
        fps: int | None = None,
        save_dir: str | None = None,
        device: str | None = None,
    ) -> None:
        was_recording = False
        with self._lock:
            was_recording = self._recording
            self._stop_recording_locked()
            if width is not None:
                self.width = int(width)
            if height is not None:
                self.height = int(height)
            if fps is not None:
                self.fps = int(fps)
            if save_dir is not None:
                self.save_dir = save_dir
                Path(self.save_dir).mkdir(parents=True, exist_ok=True)
            if device is not None:
                self.device = device
            if self._cap is not None:
                self._cap.release()
                self._cap = None
            self._open_locked()
        if was_recording:
            self.start_recording()

    def _open_locked(self) -> None:
        Path(self.save_dir).mkdir(parents=True, exist_ok=True)
        device = self.device
        cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap.release()
            raise RuntimeError(f"Could not open camera device: {device}")
        fourcc = cv2.VideoWriter_fourcc(*self.fourcc_name)
        cap.set(cv2.CAP_PROP_FOURCC, fourcc)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        # Reduce buffering for fresher preview
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
        self._cap = cap
        self._opened_device = device
        self._last_error = None

    def _loop(self) -> None:
        while True:
            with self._lock:
                if not self._running:
                    break
                cap = self._cap
            if cap is None:
                time.sleep(0.05)
                continue
            ok, frame = cap.read()
            if not ok or frame is None:
                self._last_error = "Camera read failed — check USB cable / power"
                time.sleep(0.05)
                continue
            with self._lock:
                self._frame = frame
                if self._recording and self._writer is not None:
                    self._writer.write(frame)
                    self._frames_written += 1

    def get_jpeg(self, quality: int = 80) -> bytes | None:
        with self._lock:
            frame = None if self._frame is None else self._frame.copy()
        if frame is None:
            return None
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if not ok:
            return None
        return buf.tobytes()

    def start_recording(self) -> str:
        with self._lock:
            if self._recording:
                return self._current_file or ""
            Path(self.save_dir).mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = str(Path(self.save_dir) / f"rec_{stamp}_{self.width}x{self.height}_{self.fps}fps{self.record_extension}")
            fourcc = cv2.VideoWriter_fourcc(*"MJPG")
            writer = cv2.VideoWriter(path, fourcc, float(self.fps), (self.width, self.height))
            if not writer.isOpened():
                raise RuntimeError("Could not create video file — check save location permissions")
            self._writer = writer
            self._current_file = path
            self._frames_written = 0
            self._recording = True
            return path

    def stop_recording(self) -> dict[str, Any]:
        with self._lock:
            return self._stop_recording_locked()

    def _stop_recording_locked(self) -> dict[str, Any]:
        info = {
            "file": self._current_file,
            "frames": self._frames_written,
            "was_recording": self._recording,
        }
        if self._writer is not None:
            self._writer.release()
            self._writer = None
        self._recording = False
        return info

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self._running,
                "recording": self._recording,
                "device": self._opened_device or self.device,
                "width": self.width,
                "height": self.height,
                "fps": self.fps,
                "save_dir": self.save_dir,
                "current_file": self._current_file if self._recording else None,
                "frames_written": self._frames_written if self._recording else 0,
                "last_error": self._last_error,
                "has_frame": self._frame is not None,
            }

    def list_v4l_controls(self) -> list[dict[str, Any]]:
        """Parse camera controls (type, min/max/step/value, menus) via v4l2-ctl."""
        device = self._opened_device or self.device
        try:
            out = subprocess.check_output(
                ["v4l2-ctl", "-d", device, "--list-ctrls-menus"],
                text=True,
                stderr=subprocess.STDOUT,
                timeout=5,
            )
        except (subprocess.SubprocessError, FileNotFoundError) as exc:
            return [{"error": str(exc)}]

        controls: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None

        def finish() -> None:
            nonlocal current
            if current is None:
                return
            if current.get("type") not in _SKIP_CTRL_TYPES:
                controls.append(current)
            current = None

        for raw_line in out.splitlines():
            if raw_line.lower().startswith("error "):
                continue
            header = _CTRL_HEADER.match(raw_line)
            if header:
                finish()
                name, ctype, meta = header.group(1), header.group(2).lower(), header.group(3)
                item: dict[str, Any] = {
                    "name": name,
                    "label": name.replace("_", " ").title(),
                    "type": ctype,
                    "raw": meta.strip(),
                    "inactive": "inactive" in meta,
                    "options": [],
                }
                for part in meta.replace(",", " ").split():
                    if "=" not in part:
                        continue
                    key, val = part.split("=", 1)
                    if key in {"min", "max", "step", "default", "value"}:
                        try:
                            if val.lower().startswith("0x"):
                                item[key] = int(val, 16)
                            else:
                                item[key] = int(val)
                        except ValueError:
                            item[key] = val
                if ctype == "bool":
                    item["min"] = 0
                    item["max"] = 1
                    item["step"] = 1
                current = item
                continue

            if current is not None and current.get("type") == "menu":
                menu = _MENU_ITEM.match(raw_line)
                if menu:
                    current["options"].append(
                        {"value": int(menu.group(1)), "label": menu.group(2).strip()}
                    )

        finish()
        return controls

    def set_v4l_control(self, name: str, value: str | int | bool) -> str:
        device = self._opened_device or self.device
        if not name.replace("_", "").isalnum():
            raise ValueError("Invalid control name")
        if isinstance(value, bool):
            value_str = "1" if value else "0"
        else:
            value_str = str(value).strip()
        if not value_str or not re.fullmatch(r"-?\d+", value_str):
            raise ValueError("Control value must be an integer")
        subprocess.check_call(
            ["v4l2-ctl", "-d", device, f"--set-ctrl={name}={value_str}"],
            timeout=5,
        )
        return f"{name}={value_str}"

    def apply_v4l_values(self, values: dict[str, Any]) -> list[dict[str, Any]]:
        """Set many controls. Auto modes go first so locked sliders can be written."""
        wanted: dict[str, int] = {}
        for name, raw in values.items():
            key = str(name)
            if not key.replace("_", "").isalnum():
                continue
            try:
                wanted[key] = int(raw)
            except (TypeError, ValueError):
                continue
        controls = self.list_v4l_controls()
        for _ in range(2):
            by_name = {c["name"]: c for c in controls if c.get("name") and "error" not in c}
            names = [name for name in wanted if name in by_name]
            names.sort(key=lambda name: 0 if by_name[name].get("type") in {"bool", "menu"} else 1)
            changed = False
            for name in names:
                ctrl = by_name[name]
                if ctrl.get("inactive"):
                    continue
                target = wanted[name]
                try:
                    current = int(ctrl.get("value"))
                except (TypeError, ValueError):
                    current = None
                if current == target:
                    continue
                try:
                    self.set_v4l_control(name, target)
                    changed = True
                except (subprocess.SubprocessError, OSError, ValueError):
                    continue
            controls = self.list_v4l_controls()
            if not changed:
                break
        return controls
