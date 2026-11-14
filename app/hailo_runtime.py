"""HailoRT YOLO HEF backend used inside the inference worker only."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np

from segment_draw import _COLORS


class HailoModel:
    """Configured HEF ready for repeated infer() calls."""

    def __init__(self, path: Path, meta: dict[str, Any]) -> None:
        from hailo_platform import (  # noqa: PLC0415
            HEF,
            ConfigureParams,
            FormatType,
            HailoStreamInterface,
            InferVStreams,
            InputVStreamParams,
            OutputVStreamParams,
            VDevice,
        )

        self.path = Path(path)
        self.meta = dict(meta or {})
        self.hef = HEF(str(self.path))
        self.network_name = self.hef.get_network_group_names()[0]
        self.input_info = self.hef.get_input_vstream_infos()[0]
        self.output_infos = list(self.hef.get_output_vstream_infos())
        self.input_name = self.input_info.name
        shape = list(self.input_info.shape)
        self.input_h = int(shape[0])
        self.input_w = int(shape[1])
        self.postprocess = _detect_postprocess_kind(self.output_infos)
        self._vdevice = VDevice()
        params = ConfigureParams.create_from_hef(self.hef, interface=HailoStreamInterface.PCIe)
        self._network_group = self._vdevice.configure(self.hef, params)[0]
        self._in_params = InputVStreamParams.make(self._network_group, format_type=FormatType.UINT8)
        self._out_params = OutputVStreamParams.make(
            self._network_group, format_type=FormatType.FLOAT32
        )
        self._activate_cm = self._network_group.activate()
        self._activate_cm.__enter__()
        self._infer_cm = InferVStreams(self._network_group, self._in_params, self._out_params)
        self._infer = self._infer_cm.__enter__()
        self._pecan_labels = None

    def close(self) -> None:
        for cm in (getattr(self, "_infer_cm", None), getattr(self, "_activate_cm", None)):
            if cm is None:
                continue
            try:
                cm.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
        vdevice = getattr(self, "_vdevice", None)
        if vdevice is not None:
            try:
                vdevice.release()
            except Exception:  # noqa: BLE001
                try:
                    vdevice.__exit__(None, None, None)
                except Exception:  # noqa: BLE001
                    pass

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass

    def infer(self, rgb_u8: np.ndarray) -> dict[str, np.ndarray]:
        if rgb_u8.dtype != np.uint8:
            rgb_u8 = rgb_u8.astype(np.uint8)
        if rgb_u8.ndim != 3 or rgb_u8.shape[2] != 3:
            raise RuntimeError("Hailo input must be HxWx3 uint8 RGB.")
        if rgb_u8.shape[0] != self.input_h or rgb_u8.shape[1] != self.input_w:
            rgb_u8 = cv2.resize(rgb_u8, (self.input_w, self.input_h), interpolation=cv2.INTER_LINEAR)
        batch = np.expand_dims(rgb_u8, axis=0)
        return self._infer.infer({self.input_name: batch})


def _detect_postprocess_kind(output_infos: list[Any]) -> str:
    names = [str(getattr(info, "name", "")) for info in output_infos]
    shapes = [tuple(getattr(info, "shape", ())) for info in output_infos]
    if any("nms_postprocess" in name for name in names):
        return "nms"
    if len(output_infos) >= 4 and any(len(shape) == 3 and shape[-1] == 32 for shape in shapes):
        return "yolov5_seg"
    return "raw"


def hailo_available() -> bool:
    try:
        import hailo_platform  # noqa: F401
        from pathlib import Path as _P

        return _P("/dev/hailo0").exists()
    except Exception:  # noqa: BLE001
        return False


def inspect_hef(path: Path) -> dict[str, Any]:
    from hailo_platform import HEF  # noqa: PLC0415

    hef = HEF(str(path))
    input_info = hef.get_input_vstream_infos()[0]
    outputs = list(hef.get_output_vstream_infos())
    kind = _detect_postprocess_kind(outputs)
    height, width = int(input_info.shape[0]), int(input_info.shape[1])
    num_classes = 80
    task = "detect"
    if kind == "nms":
        # Shape is typically (num_classes, 5, max_boxes)
        shape = tuple(outputs[0].shape)
        if len(shape) >= 1:
            num_classes = int(shape[0])
    elif kind == "yolov5_seg":
        task = "segment"
        # 3 anchors * (5 + C + 32) = channels on det heads
        det = next((o for o in outputs if len(o.shape) == 3 and int(o.shape[-1]) > 32), None)
        if det is not None:
            channels = int(det.shape[-1])
            # anchors=3 → C = channels/3 - 5 - 32
            if channels % 3 == 0:
                num_classes = max(1, channels // 3 - 5 - 32)
    return {
        "family": "hailo",
        "task": task,
        "architecture": hef.get_network_group_names()[0],
        "variant": Path(path).stem,
        "resolution": max(height, width),
        "input_height": height,
        "input_width": width,
        "num_classes": num_classes,
        "hef_postprocess": kind,
        "names_from_file": False,
        "class_names": [],
    }


def load_hailo(path: Path, meta: dict[str, Any]) -> HailoModel:
    if not hailo_available():
        raise RuntimeError("Hailo-8 is not available on this kit.")
    return HailoModel(path, meta)


def _letterbox(
    bgr: np.ndarray, width: int, height: int
) -> tuple[np.ndarray, float, int, int]:
    src_h, src_w = bgr.shape[:2]
    scale = min(width / float(src_w), height / float(src_h))
    new_w = max(1, int(round(src_w * scale)))
    new_h = max(1, int(round(src_h * scale)))
    resized = cv2.resize(bgr, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((height, width, 3), 114, dtype=np.uint8)
    pad_x = (width - new_w) // 2
    pad_y = (height - new_h) // 2
    canvas[pad_y : pad_y + new_h, pad_x : pad_x + new_w] = resized
    return canvas, scale, pad_x, pad_y


def _parse_nms_output(
    raw: np.ndarray,
    labels: list[str],
    threshold: float,
    frame_shape: tuple[int, int],
    scale: float,
    pad_x: int,
    pad_y: int,
    input_w: int,
    input_h: int,
) -> tuple[list[dict[str, Any]], list[tuple[int, int, int, int, int, float]]]:
    """Parse Hailo on-chip NMS tensor (C, 5, K) → items + boxes in frame pixels."""
    arr = np.asarray(raw)
    if arr.ndim == 4:
        arr = arr[0]
    if arr.ndim != 3 or arr.shape[1] < 5:
        return [], []
    height, width = frame_shape
    items: list[dict[str, Any]] = []
    boxes: list[tuple[int, int, int, int, int, float]] = []
    num_classes = arr.shape[0]
    for class_id in range(num_classes):
        for det_i in range(arr.shape[2]):
            score = float(arr[class_id, 4, det_i])
            if score < threshold:
                continue
            y_min, x_min, y_max, x_max = (float(arr[class_id, j, det_i]) for j in range(4))
            # Hailo NMS outputs are normalized to the network input.
            x1 = (x_min * input_w - pad_x) / scale
            y1 = (y_min * input_h - pad_y) / scale
            x2 = (x_max * input_w - pad_x) / scale
            y2 = (y_max * input_h - pad_y) / scale
            x1_i = int(max(0, min(width - 1, round(x1))))
            y1_i = int(max(0, min(height - 1, round(y1))))
            x2_i = int(max(0, min(width - 1, round(x2))))
            y2_i = int(max(0, min(height - 1, round(y2))))
            name = labels[class_id] if 0 <= class_id < len(labels) else f"Class {class_id + 1}"
            items.append({"class": name, "confidence": round(score, 3)})
            boxes.append((class_id, x1_i, y1_i, x2_i, y2_i, score))
    items.sort(key=lambda item: item["confidence"], reverse=True)
    return items, boxes


def _decode_yolov5_seg(
    outputs: dict[str, np.ndarray],
    labels: list[str],
    threshold: float,
    frame_shape: tuple[int, int],
    scale: float,
    pad_x: int,
    pad_y: int,
    input_w: int,
    input_h: int,
) -> tuple[list[dict[str, Any]], list[tuple[int, int, int, int, int, float]], dict[str, int]]:
    """Lightweight YOLOv5-seg decode for Hailo raw heads (COCO-style 351 channels)."""
    proto = None
    heads: list[tuple[int, np.ndarray]] = []
    for name, tensor in outputs.items():
        arr = np.asarray(tensor)
        if arr.ndim == 4:
            arr = arr[0]
        if arr.ndim != 3:
            continue
        if arr.shape[-1] == 32 and arr.shape[0] >= 80:
            proto = arr
        elif arr.shape[-1] % 3 == 0 and arr.shape[-1] > 32:
            heads.append((arr.shape[0], arr))
    if proto is None or not heads:
        return [], [], {}
    heads.sort(key=lambda item: item[0])  # 20, 40, 80
    # Default YOLOv5 anchors (from yolov5seg.json), strides 32/16/8 for 20/40/80.
    anchors = [
        [(116, 90), (156, 198), (373, 326)],
        [(30, 61), (62, 45), (59, 119)],
        [(10, 13), (16, 30), (33, 23)],
    ]
    strides = [32, 16, 8]
    num_classes = max(1, heads[0][1].shape[-1] // 3 - 5 - 32)
    detections: list[tuple[int, float, float, float, float, float, np.ndarray]] = []
    for head_i, (_grid, feat) in enumerate(heads):
        # feat: H, W, 3*(5+C+32)
        h, w, _ = feat.shape
        reshaped = feat.reshape(h, w, 3, 5 + num_classes + 32)
        stride = strides[head_i] if head_i < len(strides) else strides[-1]
        anch = anchors[head_i] if head_i < len(anchors) else anchors[-1]
        grid_y, grid_x = np.meshgrid(np.arange(h), np.arange(w), indexing="ij")
        for a_i, (aw, ah) in enumerate(anch):
            layer = reshaped[:, :, a_i, :]
            obj = 1.0 / (1.0 + np.exp(-layer[..., 4]))
            cls_logits = layer[..., 5 : 5 + num_classes]
            cls_prob = 1.0 / (1.0 + np.exp(-cls_logits))
            scores = obj[..., None] * cls_prob
            class_ids = np.argmax(scores, axis=-1)
            conf = scores.max(axis=-1)
            mask = conf >= threshold
            if not np.any(mask):
                continue
            ys, xs = np.where(mask)
            # box decode (YOLOv5)
            tx = 1.0 / (1.0 + np.exp(-layer[..., 0]))
            ty = 1.0 / (1.0 + np.exp(-layer[..., 1]))
            tw = 1.0 / (1.0 + np.exp(-layer[..., 2]))
            th = 1.0 / (1.0 + np.exp(-layer[..., 3]))
            cx = (tx * 2 - 0.5 + grid_x) * stride
            cy = (ty * 2 - 0.5 + grid_y) * stride
            bw = (tw * 2) ** 2 * aw
            bh = (th * 2) ** 2 * ah
            coeffs = layer[..., 5 + num_classes :]
            for y, x in zip(ys.tolist(), xs.tolist()):
                detections.append(
                    (
                        int(class_ids[y, x]),
                        float(conf[y, x]),
                        float(cx[y, x] - bw[y, x] / 2),
                        float(cy[y, x] - bh[y, x] / 2),
                        float(cx[y, x] + bw[y, x] / 2),
                        float(cy[y, x] + bh[y, x] / 2),
                        coeffs[y, x],
                    )
                )
    detections.sort(key=lambda row: row[1], reverse=True)
    detections = detections[:100]
    height, width = frame_shape
    items: list[dict[str, Any]] = []
    boxes: list[tuple[int, int, int, int, int, float]] = []
    class_areas: dict[str, int] = {}
    proto_flat = proto.reshape(-1, proto.shape[-1]).T  # 32 x (H*W)
    proto_h, proto_w = proto.shape[0], proto.shape[1]
    for class_id, score, x1, y1, x2, y2, coeff in detections:
        name = labels[class_id] if 0 <= class_id < len(labels) else f"Class {class_id + 1}"
        items.append({"class": name, "confidence": round(score, 3)})
        # Map from network input pixels → original frame.
        fx1 = int(max(0, min(width - 1, round((x1 - pad_x) / scale))))
        fy1 = int(max(0, min(height - 1, round((y1 - pad_y) / scale))))
        fx2 = int(max(0, min(width - 1, round((x2 - pad_x) / scale))))
        fy2 = int(max(0, min(height - 1, round((y2 - pad_y) / scale))))
        boxes.append((class_id, fx1, fy1, fx2, fy2, score))
        mask = 1.0 / (1.0 + np.exp(-(coeff @ proto_flat)))
        mask = mask.reshape(proto_h, proto_w)
        # Rough area in frame pixels from proto mask bbox crop.
        mx1 = int(max(0, min(proto_w - 1, round(x1 / input_w * proto_w))))
        my1 = int(max(0, min(proto_h - 1, round(y1 / input_h * proto_h))))
        mx2 = int(max(0, min(proto_w, round(x2 / input_w * proto_w))))
        my2 = int(max(0, min(proto_h, round(y2 / input_h * proto_h))))
        if mx2 > mx1 and my2 > my1:
            patch = mask[my1:my2, mx1:mx2] > 0.5
            area = int(patch.sum() * (input_w / proto_w) * (input_h / proto_h) / (scale * scale))
            class_areas[name] = class_areas.get(name, 0) + max(0, area)
    return items, boxes, class_areas


def run_hailo(
    model: HailoModel,
    bgr: np.ndarray,
    threshold: float,
    labels: list[str],
    task: str,
    *,
    draw: bool = True,
) -> tuple[np.ndarray, list[dict[str, Any]], dict[str, int]]:
    letterboxed, scale, pad_x, pad_y = _letterbox(bgr, model.input_w, model.input_h)
    rgb = cv2.cvtColor(letterboxed, cv2.COLOR_BGR2RGB)
    outputs = model.infer(rgb)
    items: list[dict[str, Any]] = []
    boxes: list[tuple[int, int, int, int, int, float]] = []
    class_areas: dict[str, int] = {}
    if model.postprocess == "nms":
        # Prefer the nms output tensor.
        nms_key = next((k for k in outputs if "nms_postprocess" in k), None)
        if nms_key is None:
            nms_key = next(iter(outputs))
        items, boxes = _parse_nms_output(
            outputs[nms_key],
            labels,
            threshold,
            bgr.shape[:2],
            scale,
            pad_x,
            pad_y,
            model.input_w,
            model.input_h,
        )
        for class_id, x1, y1, x2, y2, _score in boxes:
            name = labels[class_id] if 0 <= class_id < len(labels) else f"Class {class_id + 1}"
            class_areas[name] = class_areas.get(name, 0) + max(0, (x2 - x1) * (y2 - y1))
    elif model.postprocess == "yolov5_seg":
        items, boxes, class_areas = _decode_yolov5_seg(
            outputs,
            labels,
            threshold,
            bgr.shape[:2],
            scale,
            pad_x,
            pad_y,
            model.input_w,
            model.input_h,
        )
    else:
        raise RuntimeError(
            "This HEF output layout is not supported yet. "
            "Use a YOLO HEF with on-chip NMS (detect) or yolov5-seg raw heads."
        )

    annotated = bgr.copy() if draw else bgr
    if draw:
        for class_id, x1, y1, x2, y2, score in boxes:
            color = _COLORS[class_id % len(_COLORS)]
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            name = labels[class_id] if 0 <= class_id < len(labels) else f"Class {class_id + 1}"
            cv2.putText(
                annotated,
                f"{name} {score:.0%}",
                (x1, max(20, y1 - 6)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                color,
                2,
                cv2.LINE_AA,
            )
    return annotated, items, class_areas
