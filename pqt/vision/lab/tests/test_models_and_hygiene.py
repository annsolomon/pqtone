import hashlib
import io
import json
import re
import subprocess
from pathlib import Path

import numpy as np
import pytest

from pqt_vision import models
from pqt_vision.types import Frame

LAB = Path(__file__).resolve().parents[1]
REPO = LAB.parents[2]

FORBIDDEN = re.compile(r"\.(mp4|avi|mkv|mov|m4v|ts|jpe?g|png|bmp|gif|webp|parquet|onnx|pt|pth|engine|npy|npz)$", re.I)


def test_no_media_models_or_caches_are_committed_under_vision():
    """docs/privacy/vision-data.md §7: frames, clips, model files and detection caches never enter git."""
    try:
        files = subprocess.run(["git", "ls-files", "pqt/vision"], cwd=REPO, capture_output=True, text=True,
                               check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    assert [f for f in files if FORBIDDEN.search(f)] == []


def test_notebooks_have_no_outputs_and_no_secrets():
    for nb_path in sorted((LAB / "notebooks").glob("*.ipynb")):
        nb = json.loads(nb_path.read_text(encoding="utf-8"))
        for cell in nb["cells"]:
            if cell["cell_type"] == "code":
                assert cell.get("outputs") == [] and cell.get("execution_count") is None, nb_path.name
        text = nb_path.read_text(encoding="utf-8")
        assert not re.search(r"ghp_[A-Za-z0-9]{20,}|github_pat_|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY", text)


def test_registry_pins_are_sha256_and_https():
    for spec in models.MODELS.values():
        assert re.fullmatch(r"[0-9a-f]{64}", spec.sha256)
        assert spec.url.startswith("https://github.com/Megvii-BaseDetection/YOLOX/releases/download/")
        assert spec.licence == "Apache-2.0"


def _fake_spec(monkeypatch, payload: bytes, *, pinned: bytes | None = None):
    spec = models.ModelSpec("fake_model", "https://example.invalid/fake.onnx",
                            hashlib.sha256(pinned if pinned is not None else payload).hexdigest(), "Apache-2.0", (32, 32))
    monkeypatch.setitem(models.MODELS, "fake_model", spec)
    monkeypatch.setattr(models.urllib.request, "urlopen", lambda url, timeout: io.BytesIO(payload))


def test_fetch_verifies_the_hash_and_is_idempotent(tmp_path, monkeypatch):
    _fake_spec(monkeypatch, b"model bytes")
    path = models.fetch("fake_model", directory=tmp_path)
    assert path.read_bytes() == b"model bytes"
    monkeypatch.setattr(models.urllib.request, "urlopen", lambda *a, **k: pytest.fail("re-downloaded"))
    assert models.fetch("fake_model", directory=tmp_path) == path


def test_fetch_refuses_a_tampered_download(tmp_path, monkeypatch):
    _fake_spec(monkeypatch, b"tampered", pinned=b"the real thing")
    with pytest.raises(models.ModelError):
        models.fetch("fake_model", directory=tmp_path)
    assert list(tmp_path.iterdir()) == []  # no partial file left behind


def test_resolve(tmp_path, monkeypatch):
    monkeypatch.setenv("PQT_MODEL_DIR", str(tmp_path))
    with pytest.raises(models.ModelError, match="unknown model"):
        models.resolve("nope")
    with pytest.raises(models.ModelError, match="not downloaded"):
        models.resolve("yolox_s")
    (tmp_path / "yolox_s.onnx").write_bytes(b"wrong")
    with pytest.raises(models.ModelError, match="SHA-256"):
        models.resolve("yolox_s")
    with pytest.raises(models.ModelError, match="not found"):
        models.resolve(str(tmp_path / "other.onnx"))


def test_yolox_runs_end_to_end_when_the_model_is_available():
    """Integration check, only where yolox_s has been fetched (Codespaces, the bench workflow)."""
    try:
        path = models.resolve("yolox_s")
    except models.ModelError:
        pytest.skip("yolox_s not downloaded")
    from pqt_vision.detect.yolox import YoloxOnnxDetector

    det = YoloxOnnxDetector(path, name="yolox_s", conf=0.3)
    boxes = det.detect(Frame(0, 0, np.full((240, 320, 3), 128, dtype=np.uint8)))
    assert boxes == []  # a flat grey frame has nobody in it
    assert det.describe()["sha256"] == models.MODELS["yolox_s"].sha256 and det.describe()["input"] == [640, 640]
    with pytest.raises(ValueError):
        det.detect(Frame(0, 0))
