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


def test_bench_scores_counts_when_a_zone_config_exists(tmp_path: Path, monkeypatch):
    import yaml

    rows = [f"{f},1,{5 + f},5,10,30,1,1,1.0" for f in range(1, 13)]
    write_mot_sequence(tmp_path / "train", name="MOT17-02-FRCNN", gt_rows=rows, fps=2)
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "mot17-02.yaml").write_text(yaml.safe_dump({
        "apiVersion": "camera.pqt/v1", "camera": "mot17-02", "store": "mot17", "units": "px",
        "image": {"width": 64, "height": 48}, "homography": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        "zones": [{"id": "zone-1", "kind": "zone", "polygon": [[0, 0], [64, 0], [64, 48], [0, 48]]}],
        "membership": {"enterSeconds": 0.5, "exitSeconds": 0.5, "boundaryMargin": 1}}), encoding="utf-8")
    monkeypatch.setattr(bench, "resolve", lambda m: Path("unused.onnx"))
    monkeypatch.setattr(bench, "YoloxOnnxDetector", GTDetector)
    schemas = Path(__file__).resolve().parents[3] / "schemas"
    r = bench.bench_mot17(tmp_path / "train", model="fake", out=tmp_path / "out", sequences=("MOT17-02",),
                          configs=cfg, schemas=schemas, noise_out=tmp_path / "noise.yaml")
    c = r["sequences"]["MOT17-02"]["counts"]
    assert (tmp_path / "noise.yaml").is_file() and r["noiseProfile"]["name"] == "mot17-fake-full"
    assert r["noiseProfile"]["summary"]["missRate"] == {"base": 0.0}  # the perfect detector misses nobody
    assert c["schemaErrors"] == 0 and c["events"] > 0
    z = c["score"]["zones"]["zone-1"]
    assert z["n"] == 6 and z["within1"] == 1.0 and z["pass"] is True
    assert "zone-1" in (tmp_path / "out" / "report.md").read_text()


def test_roi_mode_needs_a_zone_config_and_judges_only_inside_the_region(tmp_path: Path, monkeypatch):
    import yaml

    rows = [f"{f},1,{5 + f},5,10,30,1,1,1.0" for f in range(1, 13)]
    write_mot_sequence(tmp_path / "train", name="MOT17-02-FRCNN", gt_rows=rows, fps=2)
    write_mot_sequence(tmp_path / "train", name="MOT17-04-FRCNN", gt_rows=rows, fps=2)
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "mot17-02.yaml").write_text(yaml.safe_dump({
        "apiVersion": "camera.pqt/v1", "camera": "mot17-02", "store": "mot17", "units": "px",
        "image": {"width": 64, "height": 48}, "homography": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        "zones": [{"id": "zone-1", "kind": "zone", "polygon": [[10, 20], [30, 20], [30, 40], [10, 40]]}]}), encoding="utf-8")
    monkeypatch.setattr(bench, "resolve", lambda m: Path("unused.onnx"))
    monkeypatch.setattr(bench, "YoloxOnnxDetector", GTDetector)
    r = bench.bench_mot17(tmp_path / "train", model="fake", out=tmp_path / "out", sequences=("MOT17-02", "MOT17-04"),
                          configs=cfg, mode="roi", schemas=Path(__file__).resolve().parents[3] / "schemas")
    assert r["mode"] == "roi" and r["sequences"]["MOT17-04"] == {"skipped": "roi mode needs a zone config"}
    s = r["sequences"]["MOT17-02"]
    assert s["roi"] == [0, 0, 64, 48]  # zone grown by the 64 px minimum margin, clipped to the frame
    assert "skipped" in (tmp_path / "out" / "report.md").read_text()


def test_the_noise_profile_is_validated_on_the_same_clips(tmp_path: Path, monkeypatch):
    import pytest
    import yaml

    pytest.importorskip("store_sim.noise_validate")
    rows = [f"{f},1,{5 + f},5,10,30,1,1,1.0" for f in range(1, 13)]
    write_mot_sequence(tmp_path / "train", name="MOT17-02-FRCNN", gt_rows=rows, fps=2)
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "mot17-02.yaml").write_text(yaml.safe_dump({
        "apiVersion": "camera.pqt/v1", "camera": "mot17-02", "store": "mot17", "units": "px",
        "image": {"width": 64, "height": 48}, "homography": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        "zones": [{"id": "zone-1", "kind": "zone", "polygon": [[0, 0], [64, 0], [64, 48], [0, 48]]}],
        "membership": {"enterSeconds": 0.5, "exitSeconds": 0.5, "boundaryMargin": 1}}), encoding="utf-8")
    monkeypatch.setattr(bench, "resolve", lambda m: Path("unused.onnx"))
    monkeypatch.setattr(bench, "YoloxOnnxDetector", GTDetector)
    r = bench.bench_mot17(tmp_path / "train", model="fake", out=tmp_path / "out", sequences=("MOT17-02",),
                          configs=cfg, schemas=Path(__file__).resolve().parents[3] / "schemas",
                          noise_out=tmp_path / "noise.yaml")
    v = r["noiseValidation"]
    assert v["clips"] == 1 and v["seeds"] == 30 and v["measured"]["n"] == 6
    assert v["simulated"]["n"] == 6 * 30
    # A perfect detector: the real errors are all zero, and so is what the profile it measured predicts.
    assert v["measured"]["shares"] == {"0": 1.0} and v["simulated"]["shares"] == {"0": 1.0} and not v["rejected"]
    assert r["noiseProfile"]["summary"]["visitMissRate"] == {"probability": 0.0, "n": 1}
    md = (tmp_path / "out" / "noise-validation.md").read_text()
    assert "Two-sample KS" in md and "x1" not in md
    assert "N5 same-clip validation" in (tmp_path / "out" / "report.md").read_text()
