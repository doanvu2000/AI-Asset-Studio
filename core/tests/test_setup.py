from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from test_api_basic import StubEngine

from studio_core.app import build_app
from studio_core.bootstrap import job as setup_job
from studio_core.bootstrap import plan
from studio_core.bootstrap.job import SetupError, SetupJob, SetupRequest, download
from studio_core.config import Settings
from studio_core.paths import recipes_dir

TOKEN = "ts"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
BLOBS = {"/a.bin": bytes(range(256)) * 40, "/b.bin": b"model-b" * 999}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = BLOBS.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        start = 0
        if rng := self.headers.get("Range"):
            start = int(rng.split("=")[1].rstrip("-"))
            self.send_response(206)
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(body) - start))
        self.end_headers()
        self.wfile.write(body[start:])

    def log_message(self, *a: object) -> None:
        pass


@pytest.fixture
def server() -> Iterator[str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def test_manifest_covers_every_model_the_packs_need() -> None:
    m = plan.manifest()
    names = {x["name"] for x in m["models"]}
    for p in m["packs"]:
        for w in p["workflows"]:
            assert plan.workflow_models(w), w
            assert plan.workflow_models(w) <= names, (w, plan.workflow_models(w) - names)
    for x in m["models"]:
        assert x["size"] > 0 and len(x["sha256"]) == 64
        assert x["url"].startswith(("https://huggingface.co/", "https://github.com/k2-fsa/sherpa-onnx/releases/"))
    for p in m["packs"]:
        assert set(p.get("models") or []) <= names, p["id"]
    assert {x["name"] for x in plan.pack_models([])} < {x["name"] for x in plan.pack_models(["mesh"])}
    from studio_core.adapters import embedder, tts

    sim = {x["name"]: x for x in plan.pack_models(["similar"])}
    assert set(embedder.FILES) <= set(sim)
    assert all(sim[f]["dir"] == f"similarity/{embedder.MODEL_DIR}" for f in embedder.FILES)
    voices = {x["name"]: x for x in plan.pack_models(["tts"])}
    assert {tts.VI_MODEL, tts.EN_MODEL} <= set(voices) and voices[tts.EN_MODEL]["archive"] == "tar.bz2"


def test_archive_model_extracts_and_counts_as_present(tmp_path: Path) -> None:
    import io
    import tarfile

    m = {"name": "voice-x", "dir": "tts", "archive": "tar.bz2", "size": 1}
    assert not plan.model_present(tmp_path, m)
    archive = plan.download_path(tmp_path, m)
    assert archive == tmp_path / "tts" / "voice-x.tar.bz2"
    archive.parent.mkdir(parents=True)
    with tarfile.open(archive, "w:bz2") as t:
        info = tarfile.TarInfo("voice-x/tokens.txt")
        info.size = 3
        t.addfile(info, io.BytesIO(b"a 0"))
    setup_job.extract_archive(archive, plan.model_path(tmp_path, m))
    assert (tmp_path / "tts" / "voice-x" / "tokens.txt").read_bytes() == b"a 0" and not archive.exists()
    assert plan.model_present(tmp_path, m)


def test_model_license_prefers_manifest() -> None:
    from studio_core.store import model_licenses as ml

    vi = ml.model_license("vits-piper-vi_VN-vais1000-medium")
    assert vi["license"].startswith("CC-BY-4.0") and vi["commercial"] == "unknown"
    assert ml.model_license("kokoro-int8-multi-lang-v1_0")["commercial"] == "yes"
    assert ml.model_license("vision_model_quantized.onnx")["license"] == "MIT"
    assert ml.model_license("codex")["commercial"] == "yes"
    assert ml.model_license("nope.safetensors")["commercial"] == "unknown"


def test_recipe_packs() -> None:
    def packs(name: str) -> set[str]:
        return plan.recipe_packs((recipes_dir() / f"{name}.yaml").read_text(encoding="utf-8"))

    assert packs("concept-2d") == {"base"}
    assert packs("spine-character") == {"base", "edit"}
    assert {"angles", "mesh"} <= packs("sprite-8dir-anim")
    assert "tts" in packs("npc-pack") and "tts" not in packs("concept-2d")
    known = {p["id"] for p in plan.manifest()["packs"]}
    for f in recipes_dir().glob("*.yaml"):
        assert plan.recipe_packs(f.read_text(encoding="utf-8")) <= known


def test_download_resumes_and_checks_sha(server: str, tmp_path: Path) -> None:
    body = BLOBS["/a.bin"]
    dest = tmp_path / "m" / "a.bin"
    dest.parent.mkdir()
    (dest.parent / "a.bin.part").write_bytes(body[:1000])
    seen: list[int] = []
    download(f"{server}/a.bin", dest, seen.append, len(body), _sha(body))
    assert dest.read_bytes() == body and sum(seen) == len(body)
    seen.clear()
    download(f"{server}/a.bin", dest, seen.append, len(body), _sha(body))
    assert seen == [len(body)]

    bad = tmp_path / "bad.bin"
    with pytest.raises(SetupError, match="mã kiểm tra"):
        download(f"{server}/a.bin", bad, lambda n: None, len(body), "0" * 64)
    assert not bad.exists() and not bad.with_name("bad.bin.part").exists()
    with pytest.raises(SetupError, match="404"):
        download(f"{server}/missing.bin", tmp_path / "x.bin", lambda n: None)


def _settings(tmp_path: Path) -> Settings:
    comfy = tmp_path / "comfy"
    (comfy / ".venv" / "Scripts").mkdir(parents=True)
    (comfy / "main.py").write_text("", encoding="utf-8")
    (comfy / ".venv" / "Scripts" / "python.exe").write_bytes(b"")
    data = tmp_path / "data"
    data.mkdir()
    return Settings(data_dir=data, comfy_dir=comfy, comfy_python=comfy / ".venv" / "Scripts" / "python.exe",
                    comfy_models_dir=tmp_path / "nomodels", blender_exe=tmp_path / "none.exe",
                    claude_exe=tmp_path / "none.exe")


def test_job_downloads_models_and_saves_paths(server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = [{"name": n.strip("/"), "dir": d, "url": f"{server}{n}", "size": len(BLOBS[n]), "sha256": _sha(BLOBS[n])}
            for n, d in (("/a.bin", "vae"), ("/b.bin", "loras"))]
    monkeypatch.setattr(setup_job, "pack_models", lambda packs: fake)
    s = _settings(tmp_path)
    models = tmp_path / "models"
    job = SetupJob(s, SetupRequest(tmp_path / "tools", models, [], blender=False, claude=False))
    assert [i.id for i in job.items] == ["comfyui", "pack:base", "finish"]
    job.start()
    for _ in range(200):
        if job.state != "running":
            break
        time.sleep(0.05)
    snap = job.snapshot()
    assert snap["state"] == "done", snap
    states = {i["id"]: i["state"] for i in snap["items"]}
    assert states == {"comfyui": "skipped", "pack:base": "done", "finish": "done"}
    assert snap["bytes_done"] == snap["bytes_total"] == sum(len(b) for b in BLOBS.values())
    assert (models / "vae" / "a.bin").read_bytes() == BLOBS["/a.bin"]
    saved = json.loads(s.settings_path.read_text(encoding="utf-8"))
    assert saved["comfy_models_dir"] == str(models) and saved["comfy_dir"] == str(s.comfy_dir)
    assert Path(saved["comfy_workflows_dir"]) == plan.workflows_dir()
    assert s.comfy_models_dir == models


def test_job_refuses_when_disk_is_too_small(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = [{"name": "big.bin", "dir": "x", "url": "http://127.0.0.1:9/big", "size": 10 ** 15, "sha256": "0" * 64}]
    monkeypatch.setattr(setup_job, "pack_models", lambda packs: fake)
    job = SetupJob(_settings(tmp_path), SetupRequest(tmp_path / "t", tmp_path / "m", [], False, False))
    job._run()
    assert job.state == "error" and "cần" in (job.error or "")


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    app = build_app(_settings(tmp_path), TOKEN, engine=StubEngine(), load_plugins=False)
    async with (app.router.lifespan_context(app),
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c):
        yield c


async def test_setup_api(client: httpx.AsyncClient, tmp_path: Path) -> None:
    assert (await client.get("/api/setup")).status_code == 401
    r = await client.get("/api/setup", headers=AUTH, params={"models_dir": str(tmp_path / "mm")})
    assert r.status_code == 200
    body = r.json()
    assert body["job"] is None and body["plan"]["models_dir"] == str(tmp_path / "mm")
    packs = {p["id"]: p for p in body["plan"]["packs"]}
    assert packs["base"]["required"] and not packs["base"]["installed"]
    assert packs["base"]["missing_size"] == packs["base"]["size"] > 0
    assert body["plan"]["components"]["comfyui"]["installed"]
    bad = await client.post("/api/setup/start", headers=AUTH,
                            json={"tools_dir": str(tmp_path / "t"), "models_dir": str(tmp_path / "m"), "packs": ["nope"]})
    assert bad.status_code == 400
    rel = await client.post("/api/setup/start", headers=AUTH, json={"tools_dir": "t", "models_dir": "m"})
    assert rel.status_code == 400
    assert (await client.post("/api/setup/cancel", headers=AUTH)).json() is None
