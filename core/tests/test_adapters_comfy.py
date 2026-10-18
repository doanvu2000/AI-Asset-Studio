import asyncio
import json
import random
import struct
from pathlib import Path

import httpx
import pytest
from PIL import Image

from studio_core.adapters.comfy.client import ComfyClient, history_files, parse_preview_frame
from studio_core.adapters.comfy.executor import ComfyExecutor, build_prompt
from studio_core.config import Settings
from studio_core.contracts import StepError
from test_adapters_fakes import FakeCtx, copy_blob

WORKFLOW = {
    "1": {"class_type": "LoadImage", "inputs": {"image": "default.png"}},
    "57:27": {"class_type": "CLIPTextEncode", "inputs": {"text": "x", "clip": ["2", 0]}},
    "57:3": {"class_type": "KSampler", "inputs": {"seed": 1, "steps": 4}},
    "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "z", "images": ["57:3", 0]}},
}
ENTRY = {"file": "wf.api.json", "images": [{"key": "1.image"}], "texts": [{"key": "57:27.text"}],
         "seeds": ["57:3.seed"], "outputs": [{"node": "9", "class": "SaveImage"}]}


def test_build_prompt_binds_params_images_seed_prefix():
    prompt, values = build_prompt(WORKFLOW, ENTRY, {"57:27.text": "knight", "57:3.steps": 8, "_timeout_s": 5},
                                  ["studio/abc.png"], "studio/r/s", random.Random(1))
    assert prompt["57:27"]["inputs"]["text"] == "knight"
    assert prompt["57:27"]["inputs"]["clip"] == ["2", 0]
    assert prompt["57:3"]["inputs"]["steps"] == 8
    assert prompt["1"]["inputs"]["image"] == "studio/abc.png"
    assert prompt["9"]["inputs"]["filename_prefix"] == "studio/r/s"
    assert 0 <= prompt["57:3"]["inputs"]["seed"] <= 2**53 and values["57:3.seed"] == prompt["57:3"]["inputs"]["seed"]
    assert WORKFLOW["57:27"]["inputs"]["text"] == "x"


def test_build_prompt_explicit_seed_and_errors():
    prompt, _ = build_prompt(WORKFLOW, ENTRY, {"57:3.seed": 42}, ["a.png"], "p")
    assert prompt["57:3"]["inputs"]["seed"] == 42
    with pytest.raises(StepError) as e:
        build_prompt(WORKFLOW, ENTRY, {"99.text": "x"}, ["a.png"], "p")
    assert e.value.code == "comfy_bad_param"
    with pytest.raises(StepError) as e:
        build_prompt(WORKFLOW, ENTRY, {}, ["a.png", "b.png"], "p")
    assert e.value.code == "comfy_too_many_images"
    with pytest.raises(StepError) as e:
        build_prompt(WORKFLOW, ENTRY, {}, [], "p")
    assert e.value.code == "comfy_missing_image"
    prompt, _ = build_prompt(WORKFLOW, ENTRY, {"1.image": "given.png"}, [], "p")
    assert prompt["1"]["inputs"]["image"] == "given.png"


def _settings(tmp: Path) -> Settings:
    wf = tmp / "workflows"
    wf.mkdir()
    (wf / "wf.api.json").write_text(json.dumps(WORKFLOW))
    (wf / "index.json").write_text(json.dumps({"wf": ENTRY}))
    return Settings(data_dir=tmp / "data", comfy_dir=tmp / "comfy", comfy_workflows_dir=wf)


def test_executor_cacheable_and_material(tmp_path):
    s = _settings(tmp_path)
    ex = ComfyExecutor("comfy.wf", s, resource=None)
    assert not ex.cacheable({"57:27.text": "a"})
    assert ex.cacheable({"57:27.text": "a", "57:3.seed": 1})
    m1 = ex.cache_material({})
    (s.comfy_workflows_dir / "wf.api.json").write_text(json.dumps(WORKFLOW) + " ")
    assert ex.cache_material({}) != m1
    with pytest.raises(KeyError):
        ComfyExecutor("comfy.nope", s, resource=None)


def test_parse_preview_frame():
    jpeg = b"\xff\xd8jpegdata"
    assert parse_preview_frame(struct.pack(">II", 1, 1) + jpeg) == ("image/jpeg", jpeg, None)
    assert parse_preview_frame(struct.pack(">II", 1, 2) + b"png")[0] == "image/png"
    meta = json.dumps({"image_type": "image/png", "prompt_id": "p1"}).encode()
    assert parse_preview_frame(struct.pack(">II", 4, len(meta)) + meta + b"img") == ("image/png", b"img", "p1")
    assert parse_preview_frame(b"\x00\x00") is None
    assert parse_preview_frame(struct.pack(">II", 3, 0)) is None


def test_history_files_filters_and_orders(tmp_path):
    entry = {"outputs": {
        "18": {"images": [{"filename": "t.png", "subfolder": "", "type": "temp"}]},
        "b": {"3d": [{"filename": "m.glb", "subfolder": "studio/r", "type": "output"}]},
        "a": {"images": [{"filename": "i.png", "subfolder": "studio/r", "type": "output"}]},
    }}
    files = history_files(entry, tmp_path, ["a", "b"])
    assert files == [tmp_path / "studio/r/i.png", tmp_path / "studio/r/m.glb"]


class FakeComfy:
    """httpx MockTransport standing in for the ComfyUI HTTP API; WS events are injected via client.dispatch."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.history: dict[str, dict] = {}
        self.posted: list[tuple[str, dict | None]] = []
        self.submit_status = 200
        self.submit_body: dict = {}
        self.on_submit = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        if request.method == "POST":
            self.posted.append((path, body))
        if path == "/prompt":
            if self.on_submit:
                self.on_submit(body)
            return httpx.Response(self.submit_status, json=self.submit_body or {"prompt_id": body["prompt_id"],
                                                                                  "node_errors": {}})
        if path.startswith("/history/"):
            pid = path.rsplit("/", 1)[1]
            return httpx.Response(200, json={pid: self.history[pid]} if pid in self.history else {})
        return httpx.Response(200, json={})

    def client(self) -> ComfyClient:
        c = ComfyClient("http://fake", self.output_dir, poll_s=0.05, safety_poll_s=0.2)
        c.http = httpx.AsyncClient(base_url="http://fake", transport=httpx.MockTransport(self.handler))
        return c


def _success_entry(files: list[tuple[str, str]]) -> dict:
    return {"status": {"completed": True, "status_str": "success", "messages": []},
            "outputs": {"9": {"images": [{"filename": f, "subfolder": sub, "type": "output"} for sub, f in files]}}}


async def test_client_routes_ws_events_and_returns_history(tmp_path):
    fake = FakeComfy(tmp_path)
    c = fake.client()
    c.ws_connected.set()
    events = []

    def submit(body):
        pid = body["prompt_id"]

        async def play():
            await asyncio.sleep(0.01)
            c.dispatch(json.dumps({"type": "execution_start", "data": {"prompt_id": pid}}))
            c.dispatch(json.dumps({"type": "progress", "data": {"prompt_id": "other", "value": 1, "max": 2}}))
            c.dispatch(json.dumps({"type": "executing", "data": {"prompt_id": pid, "node": "57:3"}}))
            c.dispatch(struct.pack(">II", 1, 1) + b"JPEG")
            c.dispatch(json.dumps({"type": "progress", "data": {"prompt_id": pid, "node": "57:3", "value": 2,
                                                                "max": 4}}))
            fake.history[pid] = _success_entry([("studio/r/s", "a.png")])
            c.dispatch(json.dumps({"type": "execution_success", "data": {"prompt_id": pid}}))

        asyncio.get_running_loop().create_task(play())

    fake.on_submit = submit
    pid, entry = await c.run({"9": {}}, "p-1", on_event=lambda t, d: events.append(t))
    assert pid == "p-1" and entry["status"]["status_str"] == "success"
    assert events == ["execution_start", "executing", "_preview", "progress", "execution_success"]
    assert c.active == 0
    await c.close()


async def test_client_node_errors_and_http_400(tmp_path):
    fake = FakeComfy(tmp_path)
    c = fake.client()
    fake.submit_body = {"prompt_id": "x", "node_errors": {"5": {"errors": [{"message": "bad"}]}}}
    with pytest.raises(StepError) as e:
        await c.run({}, "x")
    assert e.value.code == "comfy_node_errors" and e.value.detail["node_errors"]
    fake.submit_status, fake.submit_body = 400, {"error": {"message": "Prompt outputs failed validation"},
                                                 "node_errors": {}}
    with pytest.raises(StepError) as e:
        await c.run({}, "y")
    assert e.value.code == "comfy_node_errors"
    await c.close()


async def test_client_execution_error_via_ws(tmp_path):
    fake = FakeComfy(tmp_path)
    c = fake.client()
    c.ws_connected.set()
    fake.on_submit = lambda b: asyncio.get_running_loop().call_later(0.01, c.dispatch, json.dumps(
        {"type": "execution_error", "data": {"prompt_id": b["prompt_id"], "node_id": "3", "node_type": "KSampler",
                                             "exception_type": "torch.OutOfMemoryError",
                                             "exception_message": "OOM", "traceback": ["t"]}}))
    with pytest.raises(StepError) as e:
        await c.run({}, "p")
    assert e.value.code == "comfy_execution_error" and e.value.retryable
    await c.close()


async def test_client_polls_history_when_ws_down(tmp_path):
    fake = FakeComfy(tmp_path)
    c = fake.client()
    fake.on_submit = lambda b: fake.history.__setitem__(b["prompt_id"], _success_entry([("", "x.png")]))
    pid, entry = await asyncio.wait_for(c.run({}, "p"), 2)
    assert history_files(entry, tmp_path) == [tmp_path / "x.png"]
    fake.on_submit = lambda b: fake.history.__setitem__(b["prompt_id"], {
        "status": {"completed": False, "status_str": "error",
                   "messages": [["execution_error", {"node_type": "VAEDecode", "exception_message": "boom"}]]},
        "outputs": {}})
    with pytest.raises(StepError) as e:
        await asyncio.wait_for(c.run({}, "q"), 2)
    assert e.value.code == "comfy_execution_error"
    await c.close()


async def test_client_cancel_posts_cancel_and_interrupt(tmp_path):
    fake = FakeComfy(tmp_path)
    c = fake.client()
    c.ws_connected.set()
    cancelled = asyncio.Event()
    fake.on_submit = lambda b: asyncio.get_running_loop().call_later(0.05, cancelled.set)
    with pytest.raises(asyncio.CancelledError):
        await c.run({}, "pc", cancelled=cancelled)
    paths = [p for p, _ in fake.posted]
    assert "/api/jobs/pc/cancel" in paths and ("/interrupt", {"prompt_id": "pc"}) in fake.posted
    await c.close()


async def test_client_fail_all_on_crash(tmp_path):
    fake = FakeComfy(tmp_path)
    c = fake.client()
    c.ws_connected.set()
    fake.on_submit = lambda b: asyncio.get_running_loop().call_later(0.02, c.fail_all, "comfy_crashed", "died")
    with pytest.raises(StepError) as e:
        await c.run({}, "pz")
    assert e.value.code == "comfy_crashed" and e.value.retryable
    await c.close()


class FakeResource:
    def __init__(self, client):
        self.client = client
        self.version = "0.0-test"
        self.started = 0

    async def ensure_started(self):
        self.started += 1


async def test_executor_end_to_end_with_fake_server(tmp_path):
    s = _settings(tmp_path)
    out = s.comfy_dir / "output"
    fake = FakeComfy(out)
    client = fake.client()
    client.ws_connected.set()
    res = FakeResource(client)
    ex = ComfyExecutor("comfy.wf", s, res)
    ctx = FakeCtx(tmp_path)
    src = tmp_path / "in.png"
    Image.new("RGB", (8, 8), "red").save(src)
    ref = copy_blob(ctx, src)
    submitted = {}

    def submit(body):
        submitted.update(body)
        pid = body["prompt_id"]
        prefix = body["prompt"]["9"]["inputs"]["filename_prefix"]
        (out / prefix).parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (4, 4)).save(out / f"{prefix}_00001_.png")
        (out / f"{prefix}_00001_.glb").write_bytes(b"glTF....")
        fake.history[pid] = {"status": {"completed": True, "status_str": "success"}, "outputs": {"9": {
            "images": [{"filename": Path(prefix).name + "_00001_.png", "subfolder": str(Path(prefix).parent),
                        "type": "output"}],
            "3d": [{"filename": Path(prefix).name + "_00001_.glb", "subfolder": str(Path(prefix).parent),
                    "type": "output"}]}}}

        async def play():
            await asyncio.sleep(0.01)
            for msg in ({"type": "execution_cached", "data": {"prompt_id": pid, "nodes": ["1"]}},
                        {"type": "executing", "data": {"prompt_id": pid, "node": "57:3"}},
                        {"type": "progress", "data": {"prompt_id": pid, "node": "57:3", "value": 2, "max": 4}}):
                client.dispatch(json.dumps(msg))
            client.dispatch(struct.pack(">II", 1, 1) + b"J1")
            client.dispatch(struct.pack(">II", 1, 1) + b"J2")
            client.dispatch(json.dumps({"type": "executing", "data": {"prompt_id": pid, "node": None}}))

        asyncio.get_running_loop().create_task(play())

    fake.on_submit = submit
    result = await ex.run(ctx, {"57:27.text": "hero"}, {"image": [ref]})
    assert res.started == 1
    assert submitted["prompt"]["1"]["inputs"]["image"] == f"studio/{ref.hash}.png"
    assert (s.comfy_dir / "input" / "studio" / f"{ref.hash}.png").exists()
    assert submitted["prompt"]["9"]["inputs"]["filename_prefix"] == "studio/run1/step1"
    assert len(result.outputs["images"]) == 1 and len(result.outputs["meshes"]) == 1
    assert result.meta["prompt_id"] == submitted["prompt_id"] and "57:3.seed" in result.meta["seeds"]
    assert json.loads((ctx.job_dir / "prompt.json").read_text())["57:27"]["inputs"]["text"] == "hero"
    assert len(ctx.previews) == 1
    labels = [c[2] for c in ctx.progress_calls]
    assert "KSampler" in labels and ctx.progress_calls[-1][0] == 1.0
    await client.close()


def test_execution_error_marks_cuda_failures_retryable():
    from studio_core.adapters.comfy.client import execution_error
    cuda = execution_error({"node_type": "RemeshMesh", "exception_type": "torch.AcceleratorError",
                            "exception_message": "CUDA error: unknown error"})
    assert cuda.retryable
    assert not execution_error({"node_type": "VAEDecode", "exception_type": "ValueError",
                                "exception_message": "bad shape"}).retryable


async def test_restart_before_stops_running_server_first(tmp_path):
    calls: list[str] = []

    class Res(FakeResource):
        status = "up"

        async def stop(self):
            calls.append("stop")

        async def ensure_started(self):
            calls.append("start")
            raise StepError("halt", "stop the test here")

    ex = ComfyExecutor("comfy.wf", _settings(tmp_path), Res(None))
    with pytest.raises(StepError):
        await ex.run(FakeCtx(tmp_path), {"_restart_before": True, "1.image": "x.png"}, {})
    assert calls == ["stop", "start"]
    calls.clear()
    with pytest.raises(StepError):
        await ex.run(FakeCtx(tmp_path), {"1.image": "x.png"}, {})
    assert calls == ["start"]
