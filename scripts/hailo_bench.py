#!/usr/bin/env python3
"""Benchmark stock Hailo HEFs on this kit (raw forward FPS)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
from hailo_platform import (
    HEF,
    ConfigureParams,
    FormatType,
    HailoStreamInterface,
    InferVStreams,
    InputVStreamParams,
    OutputVStreamParams,
    VDevice,
)

MODELS = [
    Path("/usr/share/hailo-models/yolov8s_h8.hef"),
    Path("/usr/share/hailo-models/yolov6n_h8.hef"),
    Path("/usr/share/hailo-models/yolov5n_seg_h8.hef"),
]
WARMUP = 10
ITERS = 50
ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    results = []
    with VDevice() as vdevice:
        for path in MODELS:
            if not path.is_file():
                continue
            hef = HEF(str(path))
            params = ConfigureParams.create_from_hef(hef, interface=HailoStreamInterface.PCIe)
            network_group = vdevice.configure(hef, params)[0]
            input_info = hef.get_input_vstream_infos()[0]
            height, width = int(input_info.shape[0]), int(input_info.shape[1])
            dummy = np.zeros((1, height, width, 3), dtype=np.uint8)
            in_params = InputVStreamParams.make(network_group, format_type=FormatType.UINT8)
            out_params = OutputVStreamParams.make(network_group, format_type=FormatType.FLOAT32)
            with network_group.activate():
                with InferVStreams(network_group, in_params, out_params) as infer:
                    payload = {input_info.name: dummy}
                    for _ in range(WARMUP):
                        infer.infer(payload)
                    started = time.perf_counter()
                    for _ in range(ITERS):
                        infer.infer(payload)
                    elapsed = time.perf_counter() - started
            fps = ITERS / elapsed
            results.append(
                {
                    "hef": path.name,
                    "network": hef.get_network_group_names()[0],
                    "input": list(input_info.shape),
                    "fps": round(fps, 2),
                    "ms_per_frame": round(1000.0 / fps, 2),
                }
            )
            print(f"{path.name}: {fps:.1f} FPS")

    out = ROOT / "docs" / "hailo_spike_results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"device": "Hailo-8", "results": results}, indent=2))
    print("wrote", out)


if __name__ == "__main__":
    main()
