"""Persistent originals use verified bytes, independent of Cell staging."""
import hashlib

import pytest

from forge.artifacts import ArtifactStore


def test_dedup_reload_and_integrity(tmp_path):
    data = b"\x00\xffsource input"
    store = ArtifactStore(tmp_path)
    digest = store.put(data)
    assert digest == hashlib.sha256(data).hexdigest()
    assert store.put(data) == digest
    assert ArtifactStore(tmp_path).read(digest) == data
    (store.directory / digest[:2] / digest).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="integrity"):
        store.read(digest)
    with pytest.raises(ValueError, match="integrity"):
        store.put(data)


@pytest.mark.parametrize("digest", ["../escape", "/etc/passwd", "A" * 64, "a" * 63, None])
def test_content_address_cannot_supply_a_path(tmp_path, digest):
    with pytest.raises(ValueError, match="content address"):
        ArtifactStore(tmp_path).read(digest)
