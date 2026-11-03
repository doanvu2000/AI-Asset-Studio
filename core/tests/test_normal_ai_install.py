from __future__ import annotations

import io
import threading
import zipfile
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from test_hardware_fit import env, machine
from test_setup import _settings

from studio_core import hardware as hw
from studio_core.adapters import normal_ai
from studio_core.bootstrap import job as setup_job
from studio_core.bootstrap import plan
from studio_core.bootstrap.job import SetupError, SetupJob, SetupRequest
from studio_core.hardware import Needs

PACK = "normal-ai"


def _pack() -> dict[str, Any]:
    return next(p for p in plan.manifest()["packs"] if p["id"] == PACK)


def _fake_weights(root: Path) -> None:
    ai = normal_ai.NormalAi(root)
    snap = ai.hf_home / "hub" / ("models--" + normal_ai.MODEL_ID.replace("/", "--")) / "snapshots" / "abc"
    snap.mkdir(parents=True)
    (snap / "model.pt").write_bytes(b"x")


def _fake_install(root: Path, weights: bool = True) -> None:
    ai = normal_ai.NormalAi(root)
    ai.python.parent.mkdir(parents=True)
    ai.python.write_bytes(b"")
    (ai.code_dir / "moge").mkdir(parents=True)
    if weights:
        _fake_weights(root)


@pytest.fixture(autouse=True)
def no_legacy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STUDIO_NORMAL_AI_DIR", raising=False)
    monkeypatch.setattr(normal_ai, "DEFAULT_ROOT", tmp_path / "no-legacy")


def test_pack_is_listed_cpu_only_and_green_without_gpu() -> None:
    p = _pack()
    assert not p["required"] and p["hw"]["min_vram_gb"] == 0 and p["hw"]["min_ram_gb"] == 4
    assert p["license"].startswith("MIT") and p["commercial"] == "yes"
    cfg = plan.pack_component(p)
    assert cfg and cfg["model"] == normal_ai.MODEL_ID and cfg["moge"]["commit"] == "b942f00"
    e = env(machine(None), installed=False)
    rec = {x["id"]: x for x in hw.recommend(e, {})["packs"]}
    assert rec[PACK]["level"] == "green" and not rec[PACK]["installed"]
    f = hw.fit_needs(Needs({PACK}, set()), e)
    assert f["level"] == "yellow" and f["missing_packs"] == [PACK]
    assert hw.fit_needs(Needs({PACK}, set()), env(machine(None)))["level"] == "green"


def test_detects_installed_vs_missing(tmp_path: Path) -> None:
    models = tmp_path / "models dir"
    s = _settings(tmp_path)
    assert not plan.component_present(_pack(), models)
    assert not hw.pack_states(s, models)[PACK].installed
    own = models / "normal-ai"
    _fake_install(own, weights=False)
    assert "weights" in (normal_ai.locate(None, models).missing() or "")
    assert not plan.component_present(_pack(), models)
    _fake_weights(own)
    assert plan.component_present(_pack(), models)
    st = hw.pack_states(s, models)[PACK]
    assert st.installed and st.size_mb > 0 and st.missing_mb == 0
    info = {p["id"]: p for p in plan.describe(s, tmp_path / "t", models)["packs"]}[PACK]
    assert info["installed"] and info["missing_size"] == 0 and info["size"] > 0


def test_legacy_folder_counts_as_installed_and_env_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    legacy = tmp_path / "legacy"
    _fake_install(legacy)
    monkeypatch.setattr(normal_ai, "DEFAULT_ROOT", legacy)
    models = tmp_path / "models"
    assert normal_ai.locate(None, models).root == legacy
    assert plan.component_present(_pack(), models)
    own = models / "normal-ai"
    _fake_install(own)
    assert normal_ai.locate(None, models).root == own
    monkeypatch.setenv("STUDIO_NORMAL_AI_DIR", str(tmp_path / "forced"))
    assert normal_ai.locate(_settings(tmp_path), models).root == tmp_path / "forced"


def test_locate_uses_settings_models_dir(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    models = tmp_path / "m"
    models.mkdir()
    s.comfy_models_dir = models
    assert normal_ai.locate(s).root == models / "normal-ai"


class _Zip(BaseHTTPRequestHandler):
    body = b""

    def do_GET(self) -> None:
        ok = self.path == "/moge.zip"
        self.send_response(200 if ok else 404)
        self.send_header("Content-Length", str(len(self.body)) if ok else "0")
        self.end_headers()
        if ok:
            self.wfile.write(self.body)

    def log_message(self, *a: object) -> None:
        pass


@pytest.fixture
def moge_server() -> Iterator[str]:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("MoGe-b942f00/moge/__init__.py", "")
    _Zip.body = buf.getvalue()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Zip)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/moge.zip"
    srv.shutdown()


def _job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, url: str, calls: list[list[str]],
         fail_on: str | None = None, settings: Any = None) -> SetupJob:
    m = plan.manifest()
    cfg = {**m["normal_ai"], "moge": {**m["normal_ai"]["moge"], "url": url}}
    monkeypatch.setattr(setup_job, "manifest", lambda: {**m, "normal_ai": cfg})
    monkeypatch.setattr(setup_job, "pack_models", lambda packs: [])
    req = SetupRequest(tmp_path / "tools dir", tmp_path / "models dir", [PACK], blender=False, claude=False)
    job = SetupJob(settings or _settings(tmp_path), req)
    root = tmp_path / "models dir" / "normal-ai"
    monkeypatch.setattr(job, "_uv", lambda item: "uv")

    def exec_(item: Any, argv: list[str], env: dict[str, str], what: str) -> None:
        calls.append(argv)
        if fail_on and fail_on in what:
            raise SetupError(f"{what} lỗi: mất mạng")
        assert env["HF_HOME"].startswith(str(root)) and env["UV_CACHE_DIR"].startswith(str(root))
        if argv[1] == "venv":
            (root / ".venv" / "Scripts").mkdir(parents=True, exist_ok=True)
            (root / ".venv" / "Scripts" / "python.exe").write_bytes(b"")
        elif "snapshot_download" in " ".join(argv):
            _fake_weights(root)

    monkeypatch.setattr(job, "_exec", exec_)
    return job


def test_install_runs_stages_in_order_into_models_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                      moge_server: str) -> None:
    calls: list[list[str]] = []
    job = _job(tmp_path, monkeypatch, moge_server, calls)
    assert [i.id for i in job.items] == ["comfyui", f"pack:{PACK}", "finish"]
    job._run()
    snap = job.snapshot()
    assert snap["state"] == "done", snap
    root = tmp_path / "models dir" / "normal-ai"
    assert [c[1] for c in calls[:3]] == ["venv", "pip", "pip"] and str(root / ".venv") in calls[0]
    assert "https://download.pytorch.org/whl/cpu" in calls[1]
    assert (root / "src" / "moge" / "moge" / "__init__.py").is_file()
    assert normal_ai.locate(None, tmp_path / "models dir").missing() is None
    assert not (root / "tmp").exists() and not (root / "uv-cache").exists()
    item = next(i for i in snap["items"] if i["id"] == f"pack:{PACK}")
    assert item["state"] == "done" and item["done"] == item["total"]
    assert f"pack:{PACK}" not in [i.id for i in SetupJob(job.settings, job.req).items]


def test_install_dropped_network_leaves_partial_and_resumes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                           moge_server: str) -> None:
    calls: list[list[str]] = []
    job = _job(tmp_path, monkeypatch, moge_server, calls, fail_on="trọng số")
    job._run()
    snap = job.snapshot()
    assert snap["state"] == "error" and "Normal map AI" in snap["error"]
    models = tmp_path / "models dir"
    ai = normal_ai.locate(None, models)
    assert "weights" in (ai.missing() or "") and not plan.component_present(_pack(), models)
    assert (ai.code_dir / "moge").is_dir()

    calls.clear()
    job2 = _job(tmp_path, monkeypatch, "http://127.0.0.1:9/never", calls, settings=job.settings)
    assert f"pack:{PACK}" in [i.id for i in job2.items]
    job2._run()
    assert job2.state == "done", job2.snapshot()
    assert not any(c[1] == "venv" for c in calls)
    assert plan.component_present(_pack(), models)


def test_install_fails_when_moge_download_404s(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                               moge_server: str) -> None:
    job = _job(tmp_path, monkeypatch, moge_server.replace("moge.zip", "nope.zip"), [])
    job._run()
    it = next(i for i in job.snapshot()["items"] if i["id"] == f"pack:{PACK}")
    assert it["state"] == "error" and "404" in it["detail"]


def test_disk_check_counts_component(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, moge_server: str) -> None:
    job = _job(tmp_path, monkeypatch, moge_server, [])
    monkeypatch.setattr(setup_job, "free_bytes", lambda p: 100 << 20)
    job._run()
    assert job.state == "error" and "cần" in (job.error or "")
