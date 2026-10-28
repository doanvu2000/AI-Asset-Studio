from __future__ import annotations

import asyncio
import io
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
from PIL import Image, ImageDraw

from studio_core.adapters import embedder as emb
from studio_core.app import build_app
from studio_core.config import Settings
from studio_core.contracts import BlobRef, RunRow, VersionRow, VersionStatus
from studio_core.engine import similar as sm

TOKEN = "ts"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
COLORS = {"red": (220, 30, 30), "green": (30, 200, 60), "blue": (40, 60, 220)}

CONCEPT = """
id: concept-2d
name: Concept
inputs:
  prompt: {type: text}
  count: {type: int, default: 4}
  negative: {type: text, default: ""}
steps:
  - {id: ok, kind: gate.approve, params: {k: 0}}
"""
CONCEPT_STYLE = """
id: concept-2d-style
name: Concept style
inputs:
  image: {type: image, label: Ảnh style tham chiếu}
  prompt: {type: text}
steps:
  - {id: ok, kind: gate.approve, params: {k: 0}}
"""


def blob_img(color: tuple[int, int, int], shape: str = "circle", size: int = 128, alpha: bool = True) -> Image.Image:
    im = Image.new("RGBA" if alpha else "RGB", (size, size), (0, 0, 0, 0) if alpha else (255, 255, 255))
    d = ImageDraw.Draw(im)
    box = (size * 0.2, size * 0.2, size * 0.8, size * 0.8)
    (d.ellipse if shape == "circle" else d.rectangle)(box, fill=color)
    d.rectangle((size * 0.45, size * 0.1, size * 0.55, size * 0.3), fill=(20, 20, 20))
    return im


def png(img: Image.Image, fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


class FakeEmbedder:
    """Mean colour of the non-white pixels → unit vector, so same-coloured assets come out similar."""
    model_id = "fake-v1"

    def __init__(self) -> None:
        self.calls = 0

    def available(self) -> bool:
        return True

    def embed_images(self, images: list[Image.Image]) -> np.ndarray:
        self.calls += len(images)
        out = []
        for im in images:
            a = np.asarray(im.convert("RGB"), dtype=np.float32).reshape(-1, 3)
            fg = a[a.sum(1) < 720]
            m = (fg.mean(0) if len(fg) else a.mean(0)) / 255.0
            out.append(np.concatenate([m - 0.4, [0.05]]))
        return sm.normalize(np.array(out))

    def embed_text(self, text: str) -> np.ndarray:
        c = next((v for k, v in COLORS.items() if k in text), (128, 128, 128))
        return sm.normalize(np.concatenate([np.array(c) / 255.0 - 0.4, [0.05]]))


def texture(seed: int) -> Image.Image:
    rng = np.random.default_rng(seed)
    return Image.fromarray((rng.random((16, 16, 3)) * 255).astype(np.uint8)).resize((200, 200), Image.Resampling.BICUBIC)


def test_phash_dhash_robust_to_resize_and_jpeg() -> None:
    a = texture(1)
    p, d = sm.hashes(a)
    assert len(p) == len(d) == 16 and sm.hashes(a) == (p, d)
    resized = Image.open(io.BytesIO(png(a.resize((400, 400)), "JPEG")))
    assert (sm.hamming(p, sm.hashes(resized)[0]) or 0) <= 2
    assert (sm.hamming(p, sm.hashes(texture(2))[0]) or 0) > 20
    assert (sm.hamming(sm.hashes(blob_img(COLORS["red"]))[0], sm.hashes(blob_img(COLORS["blue"], "square"))[0]) or 0) > 6
    assert sm.hamming(None, p) is None


def test_cosine_topk_orders_best_first() -> None:
    mat = sm.normalize(np.array([[1, 0, 0], [0.9, 0.1, 0], [0, 1, 0], [0.5, 0.5, 0]]))
    hits = sm.cosine_topk(sm.normalize(np.array([1, 0, 0])), mat, 3)
    assert [i for i, _ in hits] == [0, 1, 3] and hits[0][1] == pytest.approx(1.0)
    assert sm.cosine_topk(np.zeros(3), np.zeros((0, 3)), 5) == []


def _v(vid: str, run: str, h: str, status: VersionStatus = VersionStatus.DRAFT, w: int = 128,
       name: str = "a.png") -> VersionRow:
    return VersionRow(id=vid, asset_name=vid, run_id=run, step_id="s", status=status, created_at=time.time(),
                      blob=BlobRef(hash=h, mime="image/png", size=1, width=w, height=128, name=name))


def test_duplicate_groups_kinds_and_same_run_rule() -> None:
    e = {"h1": sm.Entry("ffff0000ffff0000", "00", sm.normalize(np.array([1.0, 0, 0]))),
         "h2": sm.Entry("ffff0000ffff0001", "00", sm.normalize(np.array([0.99, 0.05, 0]))),
         "h3": sm.Entry("0000ffff0000ffff", "11", sm.normalize(np.array([0.0, 1, 0]))),
         "h4": sm.Entry("00ff00ff00ff00ff", "22", sm.normalize(np.array([0.02, 1, 0]))),
         "h5": sm.Entry("0f0f0f0f0f0f0f0f", "33", sm.normalize(np.array([0.0, 0, 1]))),
         "h6": sm.Entry("f0f0f0f0f0f0f0f0", "44", sm.normalize(np.array([0.02, 0, 1])))}
    vs = [_v("a", "r1", "h1"), _v("b", "r2", "h2"), _v("c", "r3", "h3"), _v("d", "r4", "h4"),
          _v("e", "r5", "h5"), _v("f", "r5", "h6"), _v("g", "r6", "h7"), _v("sheet", "r7", "h1", w=1024)]
    lib = sm.library_versions(vs)
    assert "sheet" not in {v.id for v in lib}
    groups = sm.duplicate_groups(lib, e, 0.95)
    kinds = {frozenset(m["version_id"] for m in g["members"]): g["kind"] for g in groups}
    assert kinds == {frozenset({"a", "b"}): "near", frozenset({"c", "d"}): "similar"}
    copies = {"hx": [_v("x1", "r8", "hx"), _v("x2", "r9", "hx")]}
    groups = sm.duplicate_groups(lib, e, 0.95, copies)
    assert groups[0]["kind"] == "exact" and {m["version_id"] for m in groups[0]["members"]} == {"x1", "x2"}


def test_library_versions_prefers_approved_copy() -> None:
    vs = [_v("old", "r1", "h1"), _v("ok", "r2", "h1", VersionStatus.APPROVED), _v("no", "r3", "h2", VersionStatus.REJECTED)]
    assert [v.id for v in sm.library_versions(vs)] == ["ok"]
    assert sm.library_versions(vs, only_approved=True)[0].id == "ok"


RECIPES = {"concept-2d": {"name": "Concept", "inputs": {"prompt": {"type": "text"}, "count": {"type": "int"},
                                                        "negative": {"type": "text"}}},
           "concept-2d-style": {"name": "Concept style", "inputs": {"image": {"type": "image", "label": "Ảnh style tham chiếu"},
                                                                   "prompt": {"type": "text"}}},
           "variants-2d": {"name": "Variants", "inputs": {"image": {"type": "image", "label": "Ảnh gốc"},
                                                          "style_ref": {"type": "image"}, "instructions": {"type": "text"}}}}


def _run(rid: str, inputs: dict[str, Any], applied: list[str] | None = None) -> RunRow:
    return RunRow(id="r", project_id="p1", recipe_id=rid, recipe_hash="x", inputs=inputs, created_at=0,
                  params={"$meta": {"project_applied": applied or []}})


def test_more_like_plans() -> None:
    h = "ab" * 32
    p = sm.more_like(h, _run("variants-2d", {"image": "cd" * 32, "instructions": "x", "style_ref": "ef" * 32}), RECIPES)
    assert p and p["mode"] == "style_ref" and p["inputs"] == {"image": "cd" * 32, "instructions": "x", "style_ref": h}
    p = sm.more_like(h, _run("concept-2d", {"prompt": "pixel, a chest", "count": 4, "negative": "blurry"},
                             applied=["prompt", "negative"]), RECIPES)
    assert p and p["recipe_id"] == "concept-2d-style" and p["mode"] == "style_recipe"
    assert p["inputs"] == {"prompt": "pixel, a chest", "image": h} and p["source_recipe_id"] == "concept-2d"
    assert p["project_id"] == "p1"
    p = sm.more_like(h, _run("concept-2d", {"prompt": "a"}), {"concept-2d": RECIPES["concept-2d"]})
    assert p and p["mode"] == "same_recipe" and p["inputs"] == {"prompt": "a"}
    p = sm.more_like(h, None, RECIPES)
    assert p and p["recipe_id"] == "concept-2d-style" and p["inputs"] == {"image": h}
    assert sm.more_like(h, None, {"concept-2d": RECIPES["concept-2d"]}) is None


def test_clip_tokenizer_matches_reference_ids() -> None:
    root = emb.model_dir(Settings())
    if not (root / "tokenizer.json").is_file():
        pytest.skip("CLIP tokenizer not installed")
    tok = emb.ClipTokenizer(root / "tokenizer.json")
    assert tok.encode("a photo of a cat") == [49406, 320, 1125, 539, 320, 2368, 49407]
    assert len(tok.encode("word " * 200)) == emb.CONTEXT


def test_embedder_reports_missing_files(tmp_path: Path) -> None:
    e = emb.ClipEmbedder(tmp_path)
    assert not e.available() and "tokenizer.json" in emb.missing(tmp_path)
    assert str(tmp_path) in emb.install_hint(tmp_path)


@pytest.fixture
async def app_client(tmp_path: Path) -> AsyncIterator[tuple[Any, httpx.AsyncClient]]:
    rdir = tmp_path / "builtin"
    rdir.mkdir()
    (rdir / "concept-2d.yaml").write_text(CONCEPT, encoding="utf-8")
    (rdir / "concept-2d-style.yaml").write_text(CONCEPT_STYLE, encoding="utf-8")
    settings = Settings(data_dir=tmp_path / "data", recipes_dir=rdir, comfy_workflows_dir=tmp_path / "none",
                        comfy_models_dir=tmp_path / "models")
    for d in (settings.blobs_dir, settings.thumbs_dir, settings.jobs_dir):
        d.mkdir(parents=True, exist_ok=True)
    app = build_app(settings, TOKEN)
    app.state.similar_embedder = FakeEmbedder()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            yield app, c


async def _seed(app: Any, pid: str) -> dict[str, VersionRow]:
    imgs = {"red1": blob_img(COLORS["red"]), "red2": blob_img((210, 40, 35), "circle", 160),
            "green": blob_img(COLORS["green"], "square"), "blue": blob_img(COLORS["blue"], "square")}
    out: dict[str, VersionRow] = {}
    for i, (name, im) in enumerate(imgs.items()):
        ref = await app.state.blobs.put_bytes(png(im), name=f"{name}.png", mime="image/png")
        run = RunRow(id=f"run-{name}", project_id=pid, recipe_id="concept-2d", recipe_hash="x",
                     inputs={"prompt": f"a {name} thing", "count": 2}, created_at=time.time())
        await app.state.repo.create_run(run)
        v = VersionRow(id=f"v-{name}", project_id=pid, asset_name=name, run_id=run.id, step_id="s", blob=ref,
                       created_at=time.time() + i)
        await app.state.repo.add_version(v)
        out[name] = v
    return out


async def test_api_search_duplicates_more_like(app_client: tuple[Any, httpx.AsyncClient]) -> None:
    app, c = app_client
    pid = (await c.post("/api/projects", json={"name": "Dupes"}, headers=AUTH)).json()["id"]
    vs = await _seed(app, pid)

    r = await c.get("/api/search/similar", params={"version_id": vs["red1"].id, "k": 2}, headers=AUTH)
    body = r.json()
    assert r.status_code == 200 and body["mode"] == "clip" and body["model"] == "fake-v1"
    assert [h["version_id"] for h in body["results"]] == ["v-red2", body["results"][1]["version_id"]]
    assert body["results"][0]["score"] > 0.98 and "v-red1" not in [h["version_id"] for h in body["results"]]

    r = await c.post("/api/search/similar", data={"text": "a blue box", "k": "1"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["results"][0]["version_id"] == "v-blue"
    r = await c.post("/api/search/similar", files={"file": ("q.png", png(blob_img(COLORS["green"], "square")))},
                     data={"k": "1", "project_id": pid}, headers=AUTH)
    assert r.json()["results"][0]["version_id"] == "v-green" and r.json()["results"][0]["phash_distance"] == 0
    assert (await c.post("/api/search/similar", headers=AUTH)).status_code == 400
    assert (await c.get("/api/search/similar", params={"blob": "00" * 32}, headers=AUTH)).status_code == 404

    r = await c.get(f"/api/projects/{pid}/duplicates", headers=AUTH)
    dup = r.json()
    assert r.status_code == 200 and dup["checked"] == 4 and dup["mode"] == "clip"
    assert [{m["version_id"] for m in g["members"]} for g in dup["groups"]] == [{"v-red1", "v-red2"}]
    assert (await c.get("/api/projects/nope/duplicates", headers=AUTH)).status_code == 404

    plan = (await c.get("/api/search/more-like", params={"version_id": vs["red1"].id}, headers=AUTH)).json()
    assert plan["recipe_id"] == "concept-2d-style" and plan["inputs"]["image"] == vs["red1"].blob.hash
    assert plan["inputs"]["prompt"] == "a red1 thing" and plan["project_id"] == pid
    assert plan["title"] == "Giống · red1"

    st = (await c.get("/api/search/status", headers=AUTH)).json()
    assert st["embedded"] >= 4 and st["pending"] == 0
    r = await c.post("/api/search/reindex", json={"project_id": pid, "force": True}, headers=AUTH)
    assert r.json()["queued"] == 4
    for _ in range(100):
        if app.state.image_index.pending == 0:
            break
        await asyncio.sleep(0.02)
    assert app.state.image_index.pending == 0 and len(app.state.image_index.entries) >= 4


async def test_index_persists_and_background_on_approve(app_client: tuple[Any, httpx.AsyncClient]) -> None:
    app, c = app_client
    index = app.state.image_index
    ref = await app.state.blobs.put_bytes(png(blob_img(COLORS["blue"])), name="b.png", mime="image/png")
    await app.state.repo.add_version(VersionRow(id="vb", asset_name="b", run_id="rb", step_id="s", blob=ref,
                                                created_at=time.time()))
    assert ref.hash not in index.entries
    r = await c.post("/api/versions/vb/approve", headers=AUTH)
    assert r.status_code == 200
    for _ in range(100):
        if ref.hash in index.entries:
            break
        await asyncio.sleep(0.02)
    assert index.entries[ref.hash].vec is not None
    row = await app.state.db.fetchone("SELECT model, length(vec) AS n FROM image_index WHERE hash = ?", (ref.hash,))
    assert row["model"] == "fake-v1" and row["n"] == 4 * 2


async def test_phash_only_without_model(app_client: tuple[Any, httpx.AsyncClient]) -> None:
    app, c = app_client
    app.state.image_index.embedder = None
    pid = (await c.post("/api/projects", json={"name": "NoModel"}, headers=AUTH)).json()["id"]
    vs = await _seed(app, pid)
    body = (await c.get("/api/search/similar", params={"version_id": vs["red1"].id, "k": 3}, headers=AUTH)).json()
    assert body["mode"] == "phash" and len(body["results"]) == 3
    r = await c.post("/api/search/similar", data={"text": "red"}, headers=AUTH)
    assert r.status_code == 409 and r.json()["error"]["code"] == "similar_model_missing"
    st = (await c.get("/api/search/status", headers=AUTH)).json()
    assert st["mode"] == "phash" and "hint" in st


class _FakeLink:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def request(self, method: str, path: str, **kw: Any) -> Any:
        self.calls.append((method, path, kw))
        return {"mode": "clip", "results": [], "groups": []}

    async def upload(self, path: Path) -> dict[str, Any]:
        return {"hash": "ab" * 32}

    async def close(self) -> None:
        return None


async def test_mcp_tools(tmp_path: Path) -> None:
    from mcp import Client

    from studio_core.mcp.server import build_server

    link = _FakeLink()
    img = tmp_path / "q.png"
    img.write_bytes(png(blob_img(COLORS["red"])))
    async with Client(build_server(link)) as c:  # type: ignore[arg-type]
        names = {t.name for t in (await c.list_tools()).tools}
        assert {"find_similar_assets", "find_duplicate_assets"} <= names
        await c.call_tool("find_similar_assets", {"version_id": "v1", "k": 5, "project_id": "p1"})
        assert link.calls[-1][:2] == ("GET", "/api/search/similar")
        assert link.calls[-1][2]["params"] == {"k": 5, "only_approved": False, "project_id": "p1", "version_id": "v1"}
        await c.call_tool("find_similar_assets", {"image_path": str(img)})
        assert link.calls[-1][2]["params"]["blob"] == "ab" * 32
        await c.call_tool("find_similar_assets", {"text": "red potion"})
        assert link.calls[-1][:2] == ("POST", "/api/search/similar") and link.calls[-1][2]["data"]["text"] == "red potion"
        bad = await c.call_tool("find_similar_assets", {})
        assert bad.is_error
        await c.call_tool("find_duplicate_assets", {"project_id": "p1", "threshold": 0.9})
        assert link.calls[-1][1] == "/api/projects/p1/duplicates" and link.calls[-1][2]["params"]["threshold"] == 0.9
        assert json.loads(json.dumps(link.calls[-1][2]["params"]))
