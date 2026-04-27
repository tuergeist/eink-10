"""File-backed storage for the current dashboard image + metadata.

State persisted under ``DATA_DIR``:
- ``dashboard.png`` — the bytes the board fetches verbatim
- ``meta.json``     — {last_modified, size, width, height, content_type, dither, pushed_at}

Writes are atomic (write tempfile, fsync, rename) so concurrent reads always
see a coherent state.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from PIL import Image


@dataclasses.dataclass(frozen=True)
class ImageMeta:
    last_modified: str
    size: int
    width: int
    height: int
    content_type: str
    dither: str
    pushed_at: str

    def as_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)


class Storage:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.image_path = data_dir / "dashboard.png"
        self.meta_path = data_dir / "meta.json"
        data_dir.mkdir(parents=True, exist_ok=True)

    def _atomic_write(self, target: Path, data: bytes) -> None:
        fd, tmp = tempfile.mkstemp(dir=str(self.data_dir), prefix=".tmp-", suffix=target.suffix)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, target)
        except Exception:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

    def store(self, png_bytes: bytes, dither: str) -> ImageMeta:
        # Validate + extract dimensions.
        with Image.open(io.BytesIO(png_bytes)) as probe:
            probe.verify()
        # verify() consumes the file; reopen for size.
        with Image.open(io.BytesIO(png_bytes)) as probe:
            width, height = probe.size

        digest = hashlib.sha256(png_bytes).hexdigest()[:16]
        meta = ImageMeta(
            last_modified=digest,
            size=len(png_bytes),
            width=width,
            height=height,
            content_type="image/png",
            dither=dither,
            pushed_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )

        self._atomic_write(self.image_path, png_bytes)
        self._atomic_write(
            self.meta_path,
            json.dumps(meta.as_dict(), indent=2).encode("utf-8"),
        )
        return meta

    def load_meta(self) -> Optional[ImageMeta]:
        if not self.meta_path.exists():
            return None
        try:
            data = json.loads(self.meta_path.read_text())
            return ImageMeta(**data)
        except (OSError, json.JSONDecodeError, TypeError):
            return None

    def load_image(self) -> Optional[bytes]:
        if not self.image_path.exists():
            return None
        return self.image_path.read_bytes()

    def delete(self) -> bool:
        had_any = False
        for p in (self.image_path, self.meta_path):
            if p.exists():
                p.unlink()
                had_any = True
        return had_any
