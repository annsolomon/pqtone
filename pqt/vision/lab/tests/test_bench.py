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


def test_a_tuned_setting_replaces_the_tracker_and_the_zone_timing_but_not_the_zones(tmp_path: Path, monkeypatch):
    import pytest
    import yaml

    from pqt_vision.track import ByteTrackConfig
    from pqt_vision.tune import Setting

    rows = [f"{f},1,{5 + f},5,10,30,1,1,1.0" for f in range(1, 13)]
    write_mot_sequence(tmp_path / "train", name="MOT17-02-FRCNN", gt_rows=rows, fps=2)
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    doc = {"apiVersion": "camera.pqt/v1", "camera": "mot17-02", "store": "mot17", "units": "px",
           "image": {"width": 64, "height": 48}, "homography": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
           "zones": [{"id": "zone-1", "kind": "zone", "polygon": [[0, 0], [64, 0], [64, 48], [0, 48]]}],
           "membership": {"enterSeconds": 0.5, "exitSeconds": 0.5, "boundaryMargin": 1}}
    (cfg / "mot17-02.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    monkeypatch.setattr(bench, "resolve", lambda m: Path("unused.onnx"))
    monkeypatch.setattr(bench, "YoloxOnnxDetector", GTDetector)
    s = Setting(0.4, 60, 10.0, 0.3, 1.0)  # an enter delay longer than the person's 6 s visit
    r = bench.bench_mot17(tmp_path / "train", model="fake", out=tmp_path / "out", sequences=("MOT17-02",),
                          configs=cfg, schemas=Path(__file__).resolve().parents[3] / "schemas", setting=s)
    assert r["setting"] == s.as_dict() and r["tracker"] == s.tracker().as_dict()
    events = (tmp_path / "out" / "events" / "MOT17-02.jsonl").read_text()
    assert "zone.entered" not in events  # the tuned delay reached the zone engine: the visit is never confirmed
    assert yaml.safe_load((cfg / "mot17-02.yaml").read_text()) == doc  # the committed zone config is not changed
    assert "Setting chosen on the development set" in (tmp_path / "out" / "report.md").read_text()
    base = bench.bench_mot17(tmp_path / "train", model="fake", out=tmp_path / "out2", sequences=("MOT17-02",),
                             configs=cfg, schemas=Path(__file__).resolve().parents[3] / "schemas")
    assert base["setting"] is None and "zone.entered" in (tmp_path / "out2" / "events" / "MOT17-02.jsonl").read_text()
    assert (r["sequences"]["MOT17-02"]["counts"]["score"]["zones"]["zone-1"]["mae"]
            > base["sequences"]["MOT17-02"]["counts"]["score"]["zones"]["zone-1"]["mae"])
    with pytest.raises(ValueError, match="not both"):
        bench.bench_mot17(tmp_path / "train", model="fake", out=tmp_path / "out3", sequences=("MOT17-02",),
                          tracker=ByteTrackConfig(), setting=s)
