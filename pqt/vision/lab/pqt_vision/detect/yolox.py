"""YOLOX person detector on ONNX Runtime (ADR-012).

The official YOLOX ONNX exports (release 0.1.1rc0) take a BGR image letterboxed to the model's input size
(padding value 114, no mean/std normalisation, CHW float32) and return raw head outputs, shape
(1, A, 85): per anchor point x, y, w, h (grid-relative, log-scale sizes), objectness and 80 class scores
(objectness and classes already passed through a sigmoid). `decode` turns those into image boxes.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

from ..models import sha256_file
from ..types import PERSON, Box, Frame

STRIDES = (8, 16, 32)
PAD_VALUE = 114


def letterbox(image: np.ndarray, size: tuple[int, int]) -> tuple[np.ndarray, float]:
    """Resize keeping aspect ratio into the top-left of a `size` (h, w) canvas filled with 114.

    Returns the CHW float32 tensor and the scale ratio (model px per image px).
    """
    h_in, w_in = size
    h, w = image.shape[:2]
    r = min(h_in / h, w_in / w)
    nh, nw = int(round(h * r)), int(round(w * r))
    resized = cv2.resize(image, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((h_in, w_in, 3), PAD_VALUE, dtype=np.uint8)
    canvas[:nh, :nw] = resized
    return np.ascontiguousarray(canvas.transpose(2, 0, 1), dtype=np.float32), r


def decode(raw: np.ndarray, size: tuple[int, int], strides: tuple[int, ...] = STRIDES) -> np.ndarray:
    """Raw (A, 85) head output → (A, 85) with x, y, w, h as centre and size in model pixels."""
    h_in, w_in = size
    grids, expanded = [], []
    for s in strides:
        gh, gw = h_in // s, w_in // s
        xv, yv = np.meshgrid(np.arange(gw), np.arange(gh))
        grids.append(np.stack((xv, yv), axis=2).reshape(-1, 2))
        expanded.append(np.full((gh * gw, 1), s))
    grid = np.concatenate(grids, 0).astype(np.float32)
    stride = np.concatenate(expanded, 0).astype(np.float32)
    if raw.shape[0] != grid.shape[0]:
        raise ValueError(f"model output has {raw.shape[0]} anchors; input {size} implies {grid.shape[0]}")
    out = raw.astype(np.float32, copy=True)
    out[:, :2] = (out[:, :2] + grid) * stride
    out[:, 2:4] = np.exp(out[:, 2:4]) * stride
    return out


def nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
    """Greedy non-maximum suppression; indices of kept boxes, highest score first."""
    order = np.argsort(-scores, kind="stable")
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    keep: list[int] = []
    while order.size:
        i = int(order[0])
        keep.append(i)
        rest = order[1:]
        iw = np.clip(np.minimum(x2[i], x2[rest]) - np.maximum(x1[i], x1[rest]), 0, None)
        ih = np.clip(np.minimum(y2[i], y2[rest]) - np.maximum(y1[i], y1[rest]), 0, None)
        inter = iw * ih
        union = areas[i] + areas[rest] - inter
        iou = np.where(union > 0, inter / np.where(union > 0, union, 1), 0.0)
        order = rest[iou <= iou_threshold]
    return keep


def postprocess(raw: np.ndarray, ratio: float, image_shape: tuple[int, int], size: tuple[int, int], *,
                conf: float, nms_iou: float, cls: int = PERSON) -> list[Box]:
    """Decoded person boxes in image pixels, above `conf`, after NMS, highest score first."""
    pred = decode(raw, size)
    scores = pred[:, 4] * pred[:, 5 + cls]
    mask = scores >= conf
    if not mask.any():
        return []
    p, s = pred[mask], scores[mask]
    xyxy = np.empty((len(p), 4), dtype=np.float32)
    xyxy[:, 0] = p[:, 0] - p[:, 2] / 2
    xyxy[:, 1] = p[:, 1] - p[:, 3] / 2
    xyxy[:, 2] = p[:, 0] + p[:, 2] / 2
    xyxy[:, 3] = p[:, 1] + p[:, 3] / 2
    xyxy /= ratio
    h, w = image_shape
    xyxy[:, [0, 2]] = xyxy[:, [0, 2]].clip(0, w)
    xyxy[:, [1, 3]] = xyxy[:, [1, 3]].clip(0, h)
    keep = nms(xyxy, s, nms_iou)
    return [Box(float(xyxy[i, 0]), float(xyxy[i, 1]), float(xyxy[i, 2]), float(xyxy[i, 3]),
                round(float(s[i]), 5), cls) for i in keep
            if xyxy[i, 2] > xyxy[i, 0] and xyxy[i, 3] > xyxy[i, 1]]


class YoloxOnnxDetector:
    """People from a YOLOX ONNX model. `device` is "cpu" or "cuda" (onnxruntime-gpu, e.g. on Colab)."""

    def __init__(self, model_path: str | Path, *, name: str | None = None, conf: float = 0.3,
                 nms_iou: float = 0.45, device: str = "cpu", threads: int | None = None) -> None:
        self.model_path = Path(model_path)
        self.name = name or self.model_path.stem
        self.conf, self.nms_iou = conf, nms_iou
        opts = ort.SessionOptions()
        if threads:
            opts.intra_op_num_threads = threads
        providers = ["CPUExecutionProvider"]
        if device == "cuda":
            if "CUDAExecutionProvider" not in ort.get_available_providers():
                raise RuntimeError("device=cuda needs onnxruntime-gpu with CUDA available")
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        elif device != "cpu":
            raise ValueError(f"unknown device {device!r}")
        self.device = device
        self._session = ort.InferenceSession(str(self.model_path), sess_options=opts, providers=providers)
        inp = self._session.get_inputs()[0]
        self._input_name = inp.name
        self.size = (int(inp.shape[2]), int(inp.shape[3]))
        self._sha256 = sha256_file(self.model_path)

    def detect(self, frame: Frame) -> list[Box]:
        if frame.image is None:
            raise ValueError("YOLOX needs pixels; this frame has no image")
        tensor, ratio = letterbox(frame.image, self.size)
        raw = self._session.run(None, {self._input_name: tensor[None]})[0][0]
        return postprocess(raw, ratio, frame.image.shape[:2], self.size, conf=self.conf, nms_iou=self.nms_iou)

    def describe(self) -> dict:
        return {"backend": "yolox-onnx", "model": self.name, "sha256": self._sha256,
                "input": list(self.size), "conf": self.conf, "nms": self.nms_iou, "device": self.device,
                "onnxruntime": ort.__version__}
