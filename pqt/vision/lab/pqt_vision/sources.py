"""Frame sources: a directory of images (MOT17 layout or plain), or a video file.

A source yields `Frame`s in order. `skip(index)` lets a resumed run pass over frames that are already
cached without decoding them (image directories skip the read; video files `grab()` without decoding).
"""
from __future__ import annotations

import configparser
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import cv2

from .types import Frame

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}
VIDEO_SUFFIXES = {".mp4", ".avi", ".mkv", ".mov", ".m4v", ".ts"}


class SourceError(ValueError):
    """The path is not a readable image directory, MOT sequence or video file."""


@dataclass(frozen=True)
class SourceInfo:
    """What a source is, recorded in every detection cache (never pixels)."""

    kind: str  # "images" | "mot-sequence" | "video"
    name: str
    fps: float
    frames: int | None
    width: int | None
    height: int | None

    def as_dict(self) -> dict:
        return {"kind": self.kind, "name": self.name, "fps": self.fps, "frames": self.frames,
                "width": self.width, "height": self.height}


class FrameSource:
    info: SourceInfo

    def frames(self, *, stride: int = 1, max_frames: int | None = None,
               skip: Callable[[int], bool] | None = None) -> Iterator[Frame]:
        raise NotImplementedError


def _t_ms(offset: int, fps: float) -> int:
    return int(round(offset * 1000.0 / fps))


class ImageDirSource(FrameSource):
    """Sorted images. A numeric file stem (MOT17: 000001.jpg) is the frame index; otherwise position."""

    def __init__(self, directory: Path, *, fps: float, name: str | None = None,
                 width: int | None = None, height: int | None = None) -> None:
        if fps <= 0:
            raise SourceError("fps must be positive")
        files = sorted(p for p in directory.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
        if not files:
            raise SourceError(f"no images in {directory}")
        numeric = all(p.stem.isdigit() for p in files)
        self._items = [(int(p.stem) if numeric else i, p) for i, p in enumerate(files)]
        self._items.sort(key=lambda t: t[0])
        self.info = SourceInfo("images", name or directory.name, fps, len(files), width, height)

    def frames(self, *, stride: int = 1, max_frames: int | None = None,
               skip: Callable[[int], bool] | None = None) -> Iterator[Frame]:
        first = self._items[0][0]
        emitted = 0
        for pos, (index, path) in enumerate(self._items):
            if pos % stride:
                continue
            if max_frames is not None and emitted >= max_frames:
                return
            emitted += 1
            if skip is not None and skip(index):
                continue
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is None:
                raise SourceError(f"unreadable image {path}")
            yield Frame(index=index, t_ms=_t_ms(index - first, self.info.fps), image=image)


class VideoFileSource(FrameSource):
    def __init__(self, path: Path) -> None:
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            raise SourceError(f"cannot open video {path}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or None
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        if fps <= 0:
            raise SourceError(f"video {path} reports no frame rate")
        self._path = path
        self.info = SourceInfo("video", path.stem, float(fps), frames, w or None, h or None)

    def frames(self, *, stride: int = 1, max_frames: int | None = None,
               skip: Callable[[int], bool] | None = None) -> Iterator[Frame]:
        cap = cv2.VideoCapture(str(self._path))
        try:
            index, emitted = 0, 0
            while True:
                if max_frames is not None and emitted >= max_frames:
                    return
                wanted = index % stride == 0
                if not wanted or (skip is not None and skip(index)):
                    if not cap.grab():
                        return
                    if wanted:
                        emitted += 1
                    index += 1
                    continue
                ok, image = cap.read()
                if not ok:
                    return
                emitted += 1
                yield Frame(index=index, t_ms=_t_ms(index, self.info.fps), image=image)
                index += 1
        finally:
            cap.release()


def read_seqinfo(seq_dir: Path) -> dict[str, str]:
    """MOT17 `seqinfo.ini` as a dict (name, imDir, frameRate, seqLength, imWidth, imHeight, imExt)."""
    path = seq_dir / "seqinfo.ini"
    if not path.is_file():
        raise SourceError(f"{seq_dir} has no seqinfo.ini")
    cp = configparser.ConfigParser()
    cp.read(path, encoding="utf-8")
    return dict(cp["Sequence"])


def open_source(path: str | Path, *, fps: float | None = None) -> FrameSource:
    """A MOT17 sequence directory, a plain image directory (needs fps) or a video file."""
    p = Path(path)
    if p.is_dir() and (p / "seqinfo.ini").is_file():
        si = read_seqinfo(p)
        src = ImageDirSource(p / si.get("imdir", "img1"), fps=float(si["framerate"]), name=si["name"],
                             width=int(si["imwidth"]), height=int(si["imheight"]))
        src.info = SourceInfo("mot-sequence", si["name"], float(si["framerate"]), int(si["seqlength"]),
                              int(si["imwidth"]), int(si["imheight"]))
        return src
    if p.is_dir():
        if fps is None:
            raise SourceError("an image directory needs --fps")
        return ImageDirSource(p, fps=fps)
    if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES:
        return VideoFileSource(p)
    raise SourceError(f"not a MOT sequence, image directory or video file: {p}")
