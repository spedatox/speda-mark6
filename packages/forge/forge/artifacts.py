"""Verified original inputs in the coordinator's persistent artifact area.

These inputs outlive individual Cells and are outside every worker checkout.
Content addresses identify bytes, not filenames supplied by a model or client.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import tempfile


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.directory = self.root / ".forge" / "artifacts"
        if self.directory.resolve() != self.directory:
            raise ValueError("Engineering artifact directory must not be a symlink")

    def _path(self, digest: str) -> Path:
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Invalid original input content address")
        target = self.directory / digest[:2] / digest
        if target.resolve() != target:
            raise ValueError("Engineering artifact path must not be a symlink")
        return target

    def read(self, digest: str) -> bytes:
        data = self._path(digest).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("Retained original input failed its integrity check")
        return data

    def put(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        target = self._path(digest)
        if target.exists():
            self.read(digest)
            return digest
        target.parent.mkdir(parents=True, exist_ok=True)
        scratch = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".input-", delete=False) as stream:
                scratch = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(scratch, target)
            if os.name != "nt":
                directory_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            return digest
        finally:
            if scratch is not None:
                scratch.unlink(missing_ok=True)
