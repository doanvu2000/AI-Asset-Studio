from pathlib import Path

import pytest

from studio_core.contracts import BlobRef
from studio_core.store.export import safe_filename

from test_api_basic import AUTH, _png, app_ctx  # noqa: F401  (fixture reuse)


def _ref(name: str | None, mime: str = "image/png") -> BlobRef:
    return BlobRef(hash="ab" * 32, mime=mime, size=1, name=name)


@pytest.mark.parametrize(
    ("name", "mime", "expected"),
    [
        ("hero.png", "image/png", "hero.png"),
        ("..\\..\\evil.png", "image/png", "evil.png"),
        ("../../etc/passwd", "application/octet-stream", "passwd.bin"),
        ("Hiệp sĩ: rồng?", "model/gltf-binary", "Hiệp sĩ_ rồng_.glb"),
        ("CON", "image/png", "_CON.png"),
        ("   ", "image/webp", "abababababab.webp"),
        (None, "application/json", "abababababab.json"),
        ("sheet.JSON", "application/json", "sheet.json"),
        ("Hero v1.5 final", "image/png", "Hero v1.5 final.png"),
    ],
)
def test_safe_filename(name: str | None, mime: str, expected: str) -> None:
    assert safe_filename(name, _ref(name, mime)) == expected


def test_safe_filename_caps_length() -> None:
    out = safe_filename("x" * 400 + ".png", _ref(None))
    assert len(out) <= 120 and out.endswith(".png")


async def test_export_endpoint(app_ctx, tmp_path: Path) -> None:  # noqa: F811
    _, client, _ = app_ctx
    data = _png()
    ref = (await client.post("/api/blobs", files={"file": ("1074.png", data, "image/png")}, headers=AUTH)).json()

    r = await client.post(f"/api/blobs/{ref['hash']}/export", json={"name": "Bình thuốc tím"}, headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    out = Path(body["path"])
    assert out.name == body["name"] == "Bình thuốc tím.png"
    assert out.read_bytes() == data
    assert out.is_relative_to((tmp_path / "exports" / "app").resolve())

    again = (await client.post(f"/api/blobs/{ref['hash']}/export", json={"name": "Bình thuốc tím"}, headers=AUTH)).json()
    assert again["path"] == body["path"]

    r = await client.post(f"/api/blobs/{ref['hash']}/export", json={"name": "..\\..\\x"}, headers=AUTH)
    assert Path(r.json()["path"]).parent == out.parent and r.json()["name"] == "x.png"

    default = (await client.post(f"/api/blobs/{ref['hash']}/export", json={}, headers=AUTH)).json()
    assert default["name"] == "1074.png"


async def test_export_endpoint_errors(app_ctx) -> None:  # noqa: F811
    _, client, _ = app_ctx
    assert (await client.post(f"/api/blobs/{'0' * 64}/export", json={}, headers=AUTH)).status_code == 404
    assert (await client.post("/api/blobs/not-a-hash/export", json={}, headers=AUTH)).status_code == 400
    assert (await client.post(f"/api/blobs/{'0' * 64}/export", json={})).status_code == 401
