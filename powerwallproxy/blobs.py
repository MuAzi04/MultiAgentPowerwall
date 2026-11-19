"""Content-addressed store for binary content (images, audio, files).

Agents resend the same images on every request, so everything is keyed by
sha256: decoded once, stored once, referenced by URL in events.
"""

import base64
import hashlib
import re
import struct
from collections import OrderedDict
from pathlib import Path
from typing import Optional

from .events import BlobRef

_DATA_URI = re.compile(r"^data:(?P<mime>[\w.+-]+/[\w.+-]+)?(?:;[^,]*)?;base64,(?P<data>.*)$", re.S)


def image_size(data: bytes) -> Optional[tuple[int, int]]:
    """Best-effort (width, height) from PNG/GIF/JPEG/WebP headers."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        return struct.unpack("<HH", data[6:10])
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 30:
        if data[12:16] == b"VP8X":
            w = int.from_bytes(data[24:27], "little") + 1
            h = int.from_bytes(data[27:30], "little") + 1
            return w, h
        if data[12:16] == b"VP8 ":
            w, h = struct.unpack("<HH", data[26:30])
            return w & 0x3FFF, h & 0x3FFF
        return None
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                return None
            marker = data[i + 1]
            length = struct.unpack(">H", data[i + 2 : i + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", data[i + 5 : i + 9])
                return w, h
            i += 2 + length
    return None


class BlobStore:
    def __init__(self, max_bytes: int = 512 * 1024 * 1024, persist_dir: Optional[Path] = None):
        self.max_bytes = max_bytes
        self.persist_dir = persist_dir
        self._blobs: OrderedDict[str, tuple[bytes, BlobRef]] = OrderedDict()
        self._total = 0
        if persist_dir:
            persist_dir.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes, mime: str) -> BlobRef:
        sha = hashlib.sha256(data).hexdigest()
        if sha in self._blobs:
            self._blobs.move_to_end(sha)
            return self._blobs[sha][1]
        size = image_size(data) if mime.startswith("image/") else None
        ref = BlobRef(
            sha256=sha,
            url=f"/blobs/{sha}",
            mime=mime,
            bytes=len(data),
            width=size[0] if size else None,
            height=size[1] if size else None,
        )
        self._blobs[sha] = (data, ref)
        self._total += len(data)
        if self.persist_dir:
            path = self.persist_dir / sha
            if not path.exists():
                path.write_bytes(data)
                (self.persist_dir / f"{sha}.mime").write_text(mime)
        while self._total > self.max_bytes and len(self._blobs) > 1:
            _, (old, _) = self._blobs.popitem(last=False)
            self._total -= len(old)
        return ref

    def put_data_uri(self, uri: str) -> Optional[BlobRef]:
        m = _DATA_URI.match(uri)
        if not m:
            return None
        return self.put(base64.b64decode(m["data"]), m["mime"] or "application/octet-stream")

    def get(self, sha: str) -> Optional[tuple[bytes, BlobRef]]:
        return self._blobs.get(sha)

    def load_dir(self, path: Path) -> int:
        """Load blobs persisted by a recording (see `persist_dir`)."""
        count = 0
        for mime_file in path.glob("*.mime"):
            blob = path / mime_file.stem
            if blob.exists():
                self.put(blob.read_bytes(), mime_file.read_text().strip())
                count += 1
        return count
