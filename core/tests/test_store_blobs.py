import io
from collections.abc import AsyncIterator
from pathlib import Path

import blake3
import pytest
from PIL import Image

from studio_core.store import Database, FsBlobStore, SqliteRepo


def png_bytes(w: int = 4, h: int = 3) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (w, h), (255, 0, 0, 255)).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[FsBlobStore]:
    db = Database(tmp_path / "t.db")
    await db.open()
    yield FsBlobStore(tmp_path / "blobs", tmp_path / "thumbs", SqliteRepo(db))
    await db.close()


async def test_put_file_copies_and_is_idempotent(store: FsBlobStore, tmp_path: Path) -> None:
    data = png_bytes()
    src = tmp_path / "in.png"
    src.write_bytes(data)
    ref = await store.put_file(src)
    h = blake3.blake3(data).hexdigest()
    assert ref.hash == h and ref.mime == "image/png" and ref.size == len(data)
    assert (ref.width, ref.height) == (4, 3) and ref.name == "in.png"
    assert src.exists()
    assert store.path(h) == store.root / h[:2] / h[2:4] / h
    assert store.path(h).read_bytes() == data

    again = await store.put_bytes(data, name="other.png")
    assert again.hash == h and again.name == "other.png"
    assert (await store.get(h)) is not None


async def test_glb_and_unknown_mime(store: FsBlobStore) -> None:
    glb = await store.put_bytes(b"glTF\x02\x00\x00\x00rest")
    assert glb.mime == "model/gltf-binary" and glb.width is None
    raw = await store.put_bytes(b"\x00\x01\x02")
    assert raw.mime == "application/octet-stream"
    assert await store.get("f" * 64) is None


async def test_path_rejects_traversal(store: FsBlobStore) -> None:
    with pytest.raises(ValueError):
        store.path("../../etc/passwd")
