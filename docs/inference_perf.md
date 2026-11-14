# Inference performance notes

## Software unlocks (this kit)

- Deploy / Keep checking no longer sleep 0.4s after every successful frame.
- Camera frames for inference are downscaled to the active speed size before IPC.
- Deploy live runs in metrics-only mode (no overlay JPEG/base64 each frame).
- Labels are cached on the loaded model; polygon area uses a tight crop scratch buffer.
- Torch uses up to 4 threads when not recording (2 while recording).

## Hailo-8

Stock HEF raw forward rates on this board (see `hailo_spike.md`):

- `yolov8s_h8.hef` ≈ 76 FPS
- `yolov6n_h8.hef` ≈ 131 FPS
- `yolov5n_seg_h8.hef` ≈ 36 FPS

Upload a `.hef` on the Model page to use HailoRT. Detect HEFs with on-chip NMS and yolov5-seg-style raw heads are supported; `.pt` / `.pth` still use the CPU torch path.

## Torch wheel hygiene

The venv currently has `torch 2.15.0+cu132`. CUDA is unavailable on the Pi, but this wheel still **requires** the bundled `nvidia-*` shared libraries to import. Removing those packages breaks torch. A CPU-only aarch64 wheel is not published on the usual PyTorch indexes for this platform, so the CUDA companion libs must stay until a true CPU build is available.
