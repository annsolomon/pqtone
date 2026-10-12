"""The MOT17 bench end to end on a synthetic MOT-shaped sequence, with a fake detector."""
import json
from pathlib import Path

from pqt_vision import bench
from pqt_vision.types import Box, Frame

from vision_synth import write_mot_sequence


class GTDetector:
    """Returns the ground-truth people of the synthetic sequence (a perfect detector)."""

    name = "gt"

    def __init__(self, *a, **k) -> None:
        pass

    def detect(self, frame: Frame) -> list[Box]:
        x = 5 + frame.index
        return [Box(x, 5, x + 10, 35, 0.9)]

    def describe(self) -> dict:
        return {"backend": "fake", "model": "gt", "sha256": "0" * 64, "input": [64, 64], "nms": 0.45,
                "onnxruntime": "n/a"}


def test_bench_reports_detection_and_tracking_without_boxes(tmp_path: Path, monkeypatch):
    rows = [f"{f},1,{5 + f},5,10,30,1,1,1.0" for f in range(1, 13)]
    write_mot_sequence(tmp_path / "train", name="MOT17-02-FRCNN", gt_rows=rows)
    monkeypatch.setattr(bench, "resolve", lambda m: Path("unused.onnx"))
    monkeypatch.setattr(bench, "YoloxOnnxDetector", GTDetector)
    r = bench.bench_mot17(tmp_path / "train", model="fake", out=tmp_path / "out", sequences=("MOT17-02",))
    s = r["sequences"]["MOT17-02"]
    assert s["detection"]["ap50"] == 1.0 and s["detection"]["gtPeople"] == 12
    assert s["tracking"]["IDSW"] == 0 and s["tracking"]["MOTA"] > 0.9
    report = json.loads((tmp_path / "out" / "report.json").read_text())
    text = json.dumps(report)
    assert "x1" not in text and "boxes" not in text  # aggregates only (ADR-013)
    assert "CC BY-NC-SA 3.0" in report["attribution"]
    md = (tmp_path / "out" / "report.md").read_text()
    assert "| MOT17-02 | 12 | 12 | 1.000 |" in md and "HOTA" in md
