# Hailo-8 inference spike

Measured on this kit with stock HEFs from `/usr/share/hailo-models/` (raw forward pass, uint8 input, no camera/draw).

| HEF | Input | FPS | ms/frame |
| --- | --- | ---: | ---: |
| `yolov8s_h8.hef` | [640, 640, 3] | 75.84 | 13.19 |
| `yolov6n_h8.hef` | [640, 640, 3] | 130.85 | 7.64 |
| `yolov5n_seg_h8.hef` | [640, 640, 3] | 35.87 | 27.88 |

Notes:
- yolov8s / yolov6n expose on-chip NMS outputs — best fit for a kit backend.
- yolov5n_seg exposes raw heads + proto; needs host postprocess for masks.
- Camera JPEG/IPC and UI drawing are separate from these device FPS numbers.
