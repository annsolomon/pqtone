"""Ground-truth zone counts from MOT-format annotations."""
from pathlib import Path

import numpy as np

from pqt_vision import camera, cli
from pqt_vision.counts import gt_counts
from pqt_vision.mot import build_gt, parse_gt

from vision_synth import write_mot_sequence

CAM = camera.from_doc({"apiVersion": "camera.pqt/v1", "camera": "c", "store": "s", "units": "px",
                       "image": {"width": 200, "height": 100}, "homography": np.eye(3).tolist(),
                       "zones": [{"id": "left", "kind": "zone", "polygon": [[0, 0], [100, 0], [100, 100], [0, 100]]},
                                 {"id": "right", "kind": "zone", "polygon": [[100, 0], [200, 0], [200, 100], [100, 100]]}]})


def test_counts_use_foot_points_and_sample_once_per_second():
    rows = []
    for f in range(1, 21):  # 2 s at 10 fps
        rows.append(f"{f},1,40,10,20,50,1,1,1.0")          # foot (50, 60): left
        rows.append(f"{f},2,150,10,20,50,1,7,0.9")         # static person, foot (160, 60): right
        rows.append(f"{f},3,90,10,20,50,1,1,0.1")          # barely visible: not counted
        if f > 10:
            rows.append(f"{f},4,60,40,20,50,1,1,1.0")      # arrives after 1 s, foot (70, 90): left
    gt = build_gt(parse_gt("\n".join(rows)), sequence="S", frames=20)
    out = gt_counts(gt, CAM, fps=10, every_s=1.0)
    assert out == [(0.0, "left", 1), (0.0, "right", 1), (1.0, "left", 2), (1.0, "right", 1)]


def test_cli_gt_counts(tmp_path: Path):
    seq = write_mot_sequence(tmp_path, frames=5, fps=5, gt_rows=["1,1,40,10,20,50,1,1,1.0"])
    import yaml
    (tmp_path / "cam.yaml").write_text(yaml.safe_dump(CAM.doc), encoding="utf-8")
    assert cli.main(["gt-counts", "--seq", str(seq), "--camera", str(tmp_path / "cam.yaml"),
                     "--out", str(tmp_path / "c.csv")]) == 0
    assert (tmp_path / "c.csv").read_text().splitlines()[:2] == ["offset_s,zone,count", "0.0,left,1"]
