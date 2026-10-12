"""Known model files: where they come from and the SHA-256 they must have (ADR-012).

Model files are never committed. `fetch` downloads one into the model cache ($PQT_MODEL_DIR, default
~/.cache/pqt/models) and refuses a file whose hash does not match the pin.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

YOLOX_RELEASE = "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0"


@dataclass(frozen=True)
class ModelSpec:
    name: str
    url: str
    sha256: str
    licence: str
    input: tuple[int, int]
    note: str = ""


# Hashes taken from the release files on 2026-10-12. Licence: YOLOX code and weights, Apache-2.0.
MODELS: dict[str, ModelSpec] = {
    s.name: s for s in (
        ModelSpec("yolox_nano", f"{YOLOX_RELEASE}/yolox_nano.onnx",
                  "c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d", "Apache-2.0", (416, 416)),
        ModelSpec("yolox_tiny", f"{YOLOX_RELEASE}/yolox_tiny.onnx",
                  "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7", "Apache-2.0", (416, 416)),
        ModelSpec("yolox_s", f"{YOLOX_RELEASE}/yolox_s.onnx",
                  "c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063", "Apache-2.0", (640, 640),
                  "the shipping default (ADR-012)"),
        ModelSpec("yolox_m", f"{YOLOX_RELEASE}/yolox_m.onnx",
                  "21ff6cfdeb53b013bac2249599e55f00bff3cfdfdab37ed7a4620818c1d15b3f", "Apache-2.0", (640, 640)),
        ModelSpec("yolox_l", f"{YOLOX_RELEASE}/yolox_l.onnx",
                  "7860ae79de6c89a3c1eb72ae9a2756c0ccfbe04b7791bb5880afabd97855a411", "Apache-2.0", (640, 640)),
    )
}


class ModelError(RuntimeError):
    pass


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def model_dir() -> Path:
    return Path(os.environ.get("PQT_MODEL_DIR", Path.home() / ".cache" / "pqt" / "models"))


def resolve(name_or_path: str) -> Path:
    """A path to an existing .onnx file, or a registry name already in the cache (verified)."""
    p = Path(name_or_path)
    if p.suffix == ".onnx":
        if not p.is_file():
            raise ModelError(f"model file not found: {p}")
        return p
    spec = MODELS.get(name_or_path)
    if spec is None:
        raise ModelError(f"unknown model {name_or_path!r}; known: {', '.join(MODELS)}")
    path = model_dir() / f"{spec.name}.onnx"
    if not path.is_file():
        raise ModelError(f"{spec.name} is not downloaded; run: pqt-vision fetch-model {spec.name}")
    if sha256_file(path) != spec.sha256:
        raise ModelError(f"{path} does not match the pinned SHA-256; delete it and fetch again")
    return path


def fetch(name: str, *, directory: Path | None = None) -> Path:
    """Download a registry model (if missing) and verify its hash. Returns the file path."""
    spec = MODELS.get(name)
    if spec is None:
        raise ModelError(f"unknown model {name!r}; known: {', '.join(MODELS)}")
    directory = directory or model_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{spec.name}.onnx"
    if path.is_file() and sha256_file(path) == spec.sha256:
        return path
    with tempfile.NamedTemporaryFile(dir=directory, suffix=".part", delete=False) as tmp:
        tmp_path = Path(tmp.name)
        try:
            with urllib.request.urlopen(spec.url, timeout=120) as resp:  # noqa: S310 (pinned https URL)
                shutil.copyfileobj(resp, tmp)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
    got = sha256_file(tmp_path)
    if got != spec.sha256:
        tmp_path.unlink(missing_ok=True)
        raise ModelError(f"{spec.name}: downloaded file has SHA-256 {got}, expected {spec.sha256}")
    tmp_path.replace(path)
    return path
