"""Ground-truth-only zone proposals for the open-dataset track."""
from pathlib import Path

import yaml

from pqt_vision import camera, cli
from pqt_vision.mot import build_gt, parse_gt
from pqt_vision.mot_zones import camera_doc, propose_zone, sample_frames

from vision_synth import write_mot_sequence


def crowd_rows(frames: int) -> list[str]:
    """Synthetic: a cluster of 4 standing people around (500, 600) and one walker crossing the top."""
    rows = []
    for f in range(1, frames + 1):
        for k in range(4):
            rows.append(f"{f},{k + 1},{480 + 30 * k},{500},20,100,1,1,1.0")
        rows.append(f"{f},9,{50 + 15 * f},{100},20,100,1,1,1.0")
    return rows


def test_sample_frames_one_per_second():
    assert sample_frames(95, 30) == [1, 31, 61, 91]


def test_the_proposal_covers_the_standing_cluster_and_avoids_edges():
    gt = build_gt(parse_gt("\n".join(crowd_rows(60))), sequence="SYN", frames=60)
    p = propose_zone(gt, width=1920, height=1080, fps=30)
    assert p.score == 1.0 and p.min >= 2 and p.max <= 8
    assert p.x1 > 0 and p.y1 > 0 and p.x2 < 1920 and p.y2 < 1080
    for k in range(4):  # every standing person's foot point is inside
        fx, fy = 490 + 30 * k, 600
        assert p.x1 <= fx < p.x2 and p.y1 <= fy < p.y2
    doc = camera_doc("MOT17-04", p, width=1920, height=1080)
    cfg = camera.from_doc(doc)
    assert cfg.units == "px" and cfg.zones[0].id == "zone-1" and "ADR-013" in doc["description"]


def test_cli_writes_one_valid_config_per_sequence(tmp_path: Path):
    root = tmp_path / "train"
    write_mot_sequence(root, name="MOT17-02-FRCNN", frames=40, w=1920, h=1080, gt_rows=crowd_rows(40))
    assert cli.main(["propose-zones", "--root", str(root), "--out", str(tmp_path / "cfg"), "--sequences", "MOT17-02"]) == 0
    cfg = camera.load(tmp_path / "cfg" / "mot17-02.yaml")
    assert cfg.camera == "mot17-02" and yaml.safe_load((tmp_path / "cfg" / "mot17-02.yaml").read_text())["units"] == "px"
