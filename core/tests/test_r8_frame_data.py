from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from studio_core.adapters.claude import ClaudeReply
from studio_core.adapters.combat_active import ClaudeCombatActiveExecutor, to_active_doc
from studio_core.contracts import Lane, RunStatus, StepError, StepRow, StepStatus
from studio_core.engine.library import validate
from studio_core.engine.recipe import load_recipe_file
from studio_core.export import frame_data as exp
from studio_core.export.sheet import layout
from studio_core.export.source import Source
from studio_core.ops import frame_data as fd
from test_ops_executors import FakeCtx, put_img

RECIPES = Path(__file__).resolve().parents[1] / "studio_core" / "recipes"
FW = FH = 64
SWORDS = {1: (40, 14, 46, 19), 2: (42, 20, 62, 25), 3: (44, 26, 62, 31), 4: (40, 30, 48, 35)}
ANGLES = [0, 90]


def cell_alpha(clip: str, f: int, sword: bool = True) -> np.ndarray:
    a = np.zeros((FH, FW), np.uint8)
    if clip == "walk":
        a[14:40, 24:40] = 255
        left, right = (58, 54) if f in (0, 1) else (54, 58)
        a[40:left, 24:30] = 255
        a[40:right, 34:40] = 255
    else:
        a[14:58, 24:40] = 255
    if clip == "slash" and sword and f in SWORDS:
        x0, y0, x1, y1 = SWORDS[f]
        a[y0:y1, x0:x1] = 255
    return a


def make_sheet(clips: list[tuple[str, int]], dirs: int = 2, sword: bool = True,
               blank: tuple[str, int] | None = None) -> tuple[Image.Image, dict[str, Any]]:
    cols = max(n for _, n in clips)
    rows = len(clips) * dirs
    arr = np.zeros((rows * FH, cols * FW, 4), np.uint8)
    arr[..., :3] = (200, 150, 110)
    meta_clips, row0 = [], 0
    for name, n in clips:
        meta_clips.append({"name": name, "row0": row0, "rows": dirs, "frames": n, "fps": 10, "loop": name != "slash"})
        for d in range(dirs):
            for f in range(n):
                a = np.zeros((FH, FW), np.uint8) if blank == (name, f) else cell_alpha(name, f, sword)
                arr[(row0 + d) * FH:(row0 + d + 1) * FH, f * FW:(f + 1) * FW, 3] = a[:, ::-1] if d else a
        row0 += dirs
    meta = {"frame_w": FW, "frame_h": FH, "cols": cols, "rows": rows, "count": rows * cols, "padding": 0,
            "width": cols * FW, "height": rows * FH, "pivot": [32, 58], "clips": meta_clips,
            "row_angles": ANGLES[:dirs] * len(clips)}
    return Image.fromarray(arr, "RGBA"), meta


FULL = [("idle", 2), ("slash", 6), ("walk", 4)]


def build(clips=FULL, dirs=2, sword=True, blank=None, **kw: Any) -> dict[str, Any]:
    sheet, meta = make_sheet(clips, dirs, sword, blank)
    return fd.build_frame_data(np.asarray(sheet)[..., 3], layout(meta, directional_rows=True), **kw)


def tracks_of(doc: dict[str, Any], clip: str) -> list[dict[str, Any]]:
    return [t for t in doc["tracks"] if t["clip"] == clip]


def test_parse_active_and_names():
    assert fd.parse_active("") == {}
    assert fd.parse_active("3-5, 7") == {None: [3, 4, 5, 7]}
    assert fd.parse_active("Slash: 2-3; cast: 4\nbow: 1") == {"slash": [2, 3], "cast": [4], "bow": [1]}
    for bad in ("a-", "slash:", "5-3", "x"):
        with pytest.raises(StepError):
            fd.parse_active(bad)
    assert fd.parse_names("auto") is None and fd.parse_names("Slash, cast") == {"slash", "cast"}
    assert fd.clip_role("idle", None) == "idle" and fd.clip_role("hit", None) == "other"
    assert fd.clip_role("walk", None) == "move" and fd.clip_role("slash", None) == "attack"
    assert fd.clip_role("hit", None, forced=True) == "attack"


def test_hitbox_only_on_active_frames_and_events():
    doc = build()
    for t in tracks_of(doc, "slash"):
        assert t["active"] == [2, 3] and t["active_source"] == "heuristic" and t["idle_ref"].startswith("idle_")
        assert [f["i"] for f in t["frames"] if f["hitbox"]] == [2, 3]
        assert [(e["name"], e["frame"]) for e in t["events"]] == [("attack_start", 0), ("hit_active", 2),
                                                                   ("recover", 4)]
        assert t["events"][1]["time"] == 0.2
    first = tracks_of(doc, "slash")[0]
    assert first["frames"][2]["hitbox"]["box"] == [42, 20, 20, 5]
    assert first["frames"][3]["hitbox"]["box"] == [44, 26, 18, 5]
    assert len(first["frames"][2]["hitbox"]["polygon"]) >= 3
    assert all(f["hitbox"] is None for t in tracks_of(doc, "idle") + tracks_of(doc, "walk") for f in t["frames"])
    assert doc["warnings"] == []


def test_hurtbox_covers_body_and_leaves_weapon_out():
    for count in (1, 3):
        doc = build(hurt_count=count)
        for t in tracks_of(doc, "slash"):
            for f in t["frames"]:
                assert 1 <= len(f["hurtboxes"]) <= count
                cov = np.zeros((FH, FW), bool)
                for x, y, w, h in f["hurtboxes"]:
                    cov[y:y + h, x:x + w] = True
                body = cell_alpha("idle", 0) > 0
                assert (cov & body).sum() >= 0.9 * body.sum()
                if t["dir"] == "S":
                    assert max(x + w for x, _, w, _ in f["hurtboxes"]) <= 41
    first = tracks_of(build(), "slash")[0]
    assert first["frames"][2]["hurtboxes"] == [[24, 14, 16, 44]]


def test_footsteps_follow_foot_contact():
    doc = build()
    for t in tracks_of(doc, "walk"):
        assert [e["frame"] for e in t["events"] if e["name"] == "footstep"] == [0, 2]
    assert build(footsteps=False)["tracks"][-1]["events"] == []


def test_user_and_claude_active_frames():
    doc = build(active_user={"slash": [3]})
    t = tracks_of(doc, "slash")[0]
    assert t["active"] == [3] and t["active_source"] == "user" and [f["i"] for f in t["frames"] if f["hitbox"]] == [3]
    doc = build(active_user={None: [2, 9]})
    t = tracks_of(doc, "slash")[0]
    assert t["active"] == [2] and any("outside" in w for w in doc["warnings"])
    doc = build(active_claude={"slash": [4]}, active_user={})
    t = tracks_of(doc, "slash")[0]
    assert t["active"] == [4] and t["active_source"] == "claude" and t["frames"][4]["hitbox"] is not None
    assert [e["name"] for e in t["events"]] == ["attack_start", "hit_active", "recover"]
    assert t["events"][-1]["frame"] == 5


def test_no_idle_single_direction_and_missing_weapon():
    doc = build([("slash", 6)], dirs=1)
    t = doc["tracks"][0]
    assert t["idle_ref"] is None and t["active"] == [2, 3] and any("no idle" in w for w in doc["warnings"])
    doc = build(sword=False, active_user={"slash": [2]})
    t = tracks_of(doc, "slash")[0]
    assert all(f["hitbox"] is None for f in t["frames"]) and any("not separable" in w for w in doc["warnings"])
    assert t["frames"][0]["hurtboxes"] == [[24, 14, 16, 44]]
    doc = build(sword=False)
    assert tracks_of(doc, "slash")[0]["active"] == [] and tracks_of(doc, "slash")[0]["active_source"] == "none"
    assert not [e for e in tracks_of(doc, "slash")[0]["events"] if e["name"] != "attack_start"]


def test_missing_idle_direction_uses_nearest_and_empty_frame_survives():
    sheet, meta = make_sheet(FULL, dirs=2)
    meta["row_angles"] = [0, 90, 0, 90, 0, 90]
    meta["row_angles"][1] = 45
    doc = fd.build_frame_data(np.asarray(sheet)[..., 3], layout(meta, directional_rows=True))
    assert tracks_of(doc, "slash")[0]["idle_ref"] is not None
    doc = build(blank=("walk", 1))
    walk = tracks_of(doc, "walk")[0]
    assert walk["frames"][1]["hurtboxes"] == [] and any("empty frame" in w for w in doc["warnings"])
    with pytest.raises(StepError):
        fd.build_frame_data(np.zeros((FH, FW), np.uint8), layout({"frame_w": FW, "frame_h": FH}))


def test_preview_and_contact_sheet_sizes():
    sheet, meta = make_sheet(FULL)
    lay = layout(meta, directional_rows=True)
    doc = fd.build_frame_data(np.asarray(sheet)[..., 3], lay)
    prev = fd.render_preview(sheet, lay, doc)
    scale = max(1, min(4, 2048 // sheet.width))
    assert prev.size == (sheet.width * scale, sheet.height * scale)
    red = np.asarray(prev).astype(int)
    assert ((red[..., 0] > 200) & (red[..., 1] < 80) & (red[..., 2] < 80)).any()
    strip = fd.contact_sheet(sheet, lay, ["slash"])
    assert strip.size == (6 * FW, FH)


async def _ctx_inputs(tmp_path: Path, **kw: Any):
    ctx = FakeCtx(tmp_path)
    sheet, meta = make_sheet(FULL, **kw)
    sref = await put_img(ctx, sheet, "sheet.png")
    mref = await ctx.blobs.put_bytes(json.dumps(meta).encode(), name="sheet.json", mime="application/json")
    return ctx, sref, mref


@pytest.mark.asyncio
async def test_op_outputs(tmp_path: Path):
    ctx, sref, mref = await _ctx_inputs(tmp_path)
    res = await fd.FrameData().run(ctx, {"hurtboxes": 3}, {"sheet": [sref], "meta": [mref]})
    assert res.outputs["frames"][0].name == fd.FRAME_FILE and res.outputs["sheet"] == [sref]
    assert res.outputs["meta"] == [mref] and res.outputs["preview"][0].name == fd.PREVIEW_FILE
    doc = json.loads(ctx.blobs.path(res.outputs["frames"][0].hash).read_bytes())
    assert doc["type"] == fd.FRAME_TYPE and doc["sheet_meta"]["frame_w"] == FW and len(doc["tracks"]) == 6
    assert res.meta["hitboxes"] == 4 and all(len(f["hurtboxes"]) <= 3 for t in doc["tracks"] for f in t["frames"])
    claude = await ctx.blobs.put_bytes(json.dumps({"type": fd.ACTIVE_TYPE, "clips": [{"clip": "slash",
                                                                                        "active": [4]}]}).encode(),
                                       name="combat_active.json", mime="application/json")
    res = await fd.FrameData().run(ctx, {}, {"sheet": [sref], "meta": [mref], "claude": [claude]})
    assert res.meta["active"] == {"slash_S": [4], "slash_W": [4]}
    res = await fd.FrameData().run(ctx, {"active": "slash: 3"}, {"sheet": [sref], "meta": [mref], "claude": [claude]})
    assert res.meta["active"] == {"slash_S": [3], "slash_W": [3]}
    with pytest.raises(StepError):
        await fd.FrameData().run(ctx, {}, {"sheet": [sref]})
    with pytest.raises(StepError):
        await fd.FrameData().run(ctx, {"active": "oops"}, {"sheet": [sref], "meta": [mref]})


def test_claude_reply_filter():
    sizes = {"slash": 6, "cast": 8}
    doc = to_active_doc({"clips": [{"clip": "slash", "active": [2, 3, 3, 99, -1], "note": "peak"},
                                   {"clip": "ghost", "active": [1]}, {"clip": "cast", "active": []}]}, sizes)
    assert doc["clips"] == [{"clip": "slash", "active": [2, 3], "note": "peak"}]
    with pytest.raises(StepError) as e:
        to_active_doc({"clips": [{"clip": "ghost", "active": [1]}]}, sizes)
    assert e.value.retryable


class FakeQuery:
    def __init__(self, reply: dict[str, Any]):
        self.reply, self.calls = reply, []

    async def __call__(self, content: list[dict[str, Any]], options: Any) -> ClaudeReply:
        self.calls.append((content, options))
        return ClaudeReply(self.reply, "", {"cost_usd": 0.02, "model": options.model})


class FailingQuery(FakeQuery):
    async def __call__(self, content: list[dict[str, Any]], options: Any) -> ClaudeReply:
        self.calls.append((content, options))
        raise StepError("claude_error", "overloaded", retryable=True)


@pytest.mark.asyncio
async def test_claude_step_skips_when_user_wrote_frames(tmp_path: Path):
    ctx, sref, mref = await _ctx_inputs(tmp_path)
    q = FakeQuery({"clips": [{"clip": "slash", "active": [3]}]})
    ex = ClaudeCombatActiveExecutor(None, q)
    assert ex.kind == "claude.combat_active" and ex.lane == Lane.CLOUD
    assert (await ex.run(ctx, {"active": "3"}, {"sheet": [sref], "meta": [mref]})).outputs == {"json": []}
    assert (await ex.run(ctx, {"claude": False}, {"sheet": [sref], "meta": [mref]})).outputs == {"json": []}
    assert q.calls == []
    res = await ex.run(ctx, {}, {"sheet": [sref], "meta": [mref]})
    content = q.calls[0][0]
    assert any(isinstance(c, dict) and c.get("type") == "image" for c in content) and "slash (6 frames)" in json.dumps(content)
    assert json.loads(ctx.blobs.path(res.outputs["json"][0].hash).read_bytes())["clips"][0]["active"] == [3]


def test_recipe_loads_and_lints():
    r = load_recipe_file(RECIPES / "combat-frame-data.yaml")
    assert [s.id for s in r.steps] == ["guess", "frames", "approve"]
    assert r.steps[0].allow_fail and r.steps[0].kind == "claude.combat_active" and r.steps[1].kind == "op.frame_data"
    _, issues = validate((RECIPES / "combat-frame-data.yaml").read_text(encoding="utf-8"), None)
    assert issues == []


def _env(tmp_path: Path, query: FakeQuery):
    from test_engine_runner import Env

    tmp_path.mkdir(parents=True, exist_ok=True)

    env = Env(tmp_path, [], {"combat-frame-data": (RECIPES / "combat-frame-data.yaml").read_text(encoding="utf-8")})
    env.registry.register(ClaudeCombatActiveExecutor(env.settings, query))
    env.registry.register(fd.FrameData())
    return env


async def _run(env: Any, **extra: Any) -> tuple[str, dict[str, Any]]:
    sheet, meta = make_sheet(FULL)
    buf = io.BytesIO()
    sheet.save(buf, "PNG")
    s = (await env.blobs.put_bytes(buf.getvalue(), name="sheet.png", mime="image/png")).hash
    m = (await env.blobs.put_bytes(json.dumps(meta).encode(), name="sheet.json", mime="application/json")).hash
    run_id = await env.engine.start_run("combat-frame-data", {"sheet": s, "meta": m, "review": False, **extra})
    await asyncio.wait_for(env.engine.wait_idle(run_id), 120)
    return run_id, env.steps(run_id)


@pytest.mark.asyncio
async def test_recipe_runs_with_claude_and_survives_its_failure(tmp_path: Path):
    q = FakeQuery({"clips": [{"clip": "slash", "active": [4]}]})
    env = _env(tmp_path / "a", q)
    run_id, steps = await _run(env)
    assert env.status(run_id) == RunStatus.SUCCEEDED, {k: s.error for k, s in steps.items() if s.error}
    assert len(q.calls) == 1 and steps["frames"].meta["active"]["slash_S"] == [4]
    assert steps["approve"].outputs["frames"] and steps["approve"].outputs["sheet"]

    bad = FailingQuery({})
    env = _env(tmp_path / "b", bad)
    run_id, steps = await _run(env)
    assert env.status(run_id) == RunStatus.SUCCEEDED, {k: s.error for k, s in steps.items() if s.error}
    assert steps["guess"].status == StepStatus.SKIPPED and steps["frames"].meta["active"]["slash_S"] == [2, 3]

    q2 = FakeQuery({})
    env = _env(tmp_path / "c", q2)
    run_id, steps = await _run(env, active="slash: 3")
    assert q2.calls == [] and steps["frames"].meta["active"]["slash_S"] == [3]


def _source(tmp_path: Path) -> Source:
    sheet, meta = make_sheet(FULL)
    png = tmp_path / "hero.png"
    sheet.save(png)
    doc = build()
    src = Source(asset="Hero", uid="u1", sheet=png, meta=meta, ppu=100.0, directional_rows=True)
    src.frame_data = doc
    return src


def test_frame_data_set_reads_gate_passthrough(tmp_path: Path):
    from test_ops_executors import FakeBlobs

    blobs = FakeBlobs(tmp_path)
    doc = build()
    doc["sheet_meta"] = {"frame_w": FW}
    ref = asyncio.run(blobs.put_bytes(json.dumps(doc).encode(), name=fd.FRAME_FILE, mime="application/json"))
    sheet = asyncio.run(blobs.put_bytes(b"png", name="sheet.png", mime="image/png"))
    row = StepRow(id="id-approve", run_id="r", key="approve", kind="gate.approve", lane=Lane.CPU,
                  status=StepStatus.SUCCEEDED, deps=[], inputs={}, outputs={"frames": [ref], "sheet": [sheet]})
    got, path = exp.frame_data_set(row, blobs)
    assert got is not None and got["type"] == fd.FRAME_TYPE and path == blobs.path(sheet.hash)
    other = StepRow(id="id-x", run_id="r", key="x", kind="gate.approve", lane=Lane.CPU, status=StepStatus.SUCCEEDED,
                    deps=[], inputs={}, outputs={"frames": [sheet]})
    assert exp.frame_data_set(other, blobs) == (None, None)


def test_unity_export(tmp_path: Path):
    from studio_core.export import unity

    src = _source(tmp_path)
    base = unity.export(src)
    assert "Animations/slash_S.anim" in dict(base) or any(r.startswith("Animations/slash_") for r, _ in base)
    files, notes = exp.unity_files(src)
    f = dict(files)
    assert {"FrameData/Hero_frame_data.json", "FrameData/Hero_frame_data.asset", "FrameData/Hero_frame_data.asset.meta",
            "Scripts/FrameData.cs", "Scripts/FrameData.cs.meta", "Scripts/FrameEventReceiver.cs",
            "Scripts/FrameEventReceiver.cs.meta"} <= set(f) and notes
    assert f"guid: {exp.DATA_GUID}" in f["Scripts/FrameData.cs.meta"].decode()
    assert f"guid: {exp.RECEIVER_GUID}" in f["Scripts/FrameEventReceiver.cs.meta"].decode()
    assert exp.DATA_GUID != exp.RECEIVER_GUID and exp.DATA_GUID != "5a17c0de4e9d4c1fa3b2c0ffee000001"
    asset = f["FrameData/Hero_frame_data.asset"].decode()
    assert asset.startswith("%YAML 1.1") and f"m_Script: {{fileID: 11500000, guid: {exp.DATA_GUID}, type: 3}}" in asset
    assert asset.count("  - name: ") == 6 and "hasHitbox: 1" in asset and "name: footstep" in asset
    assert "class FrameData : ScriptableObject" in f["Scripts/FrameData.cs"].decode()
    assert "sheet_meta" not in json.loads(f["FrameData/Hero_frame_data.json"])

    patched = dict(exp.patch_clips(base, src))
    slash = next(v for r, v in patched.items() if r.startswith("Animations/slash_") and r.endswith(".anim")).decode()
    assert "m_Events: []" not in slash and slash.count("functionName: OnFrameEvent") == 3
    assert "data: hit_active" in slash and "time: 0.2\n" in slash
    idle = next(v for r, v in patched.items() if r.startswith("Animations/idle_") and r.endswith(".anim")).decode()
    assert "m_Events: []" in idle
    walk = next(v for r, v in patched.items() if r.startswith("Animations/walk_") and r.endswith(".anim")).decode()
    assert walk.count("data: footstep") == 2


def test_godot_export(tmp_path: Path):
    src = _source(tmp_path)
    files, notes = exp.godot_files(src)
    f = dict(files)
    assert {"FrameData/Hero_frame_data.json", "asset_studio_frame_events.gd", "Hero_combat.tscn"} <= set(f) and notes
    t = f["Hero_combat.tscn"].decode()
    assert t.startswith("[gd_scene load_steps=") and "format=3]" in t.splitlines()[0]
    ext, sub = t.count("[ext_resource"), t.count("[sub_resource")
    assert f"load_steps={ext + sub + 1} " in t.splitlines()[0]
    assert t.count('[sub_resource type="Animation"') == 6 and "&\"slash_S\": SubResource(" in t
    assert 'tracks/0/path = NodePath("Hurtbox/Shape0:disabled")' in t
    assert 'NodePath("Hitbox/Shape:shape:size")' in t and 'NodePath("Hitbox/Shape:position")' in t
    assert t.count('"method": &"emit_frame_event"') == sum(len(tr["events"]) for tr in src.frame_data["tracks"])
    assert '[node name="Hero" type="Node2D"]' in t and 'type="AnimationPlayer" parent="."' in t
    assert 'type="CollisionShape2D" parent="Hitbox"' in t and 'script = ExtResource("1_script")' in t
    assert "func emit_frame_event" in f["asset_studio_frame_events.gd"].decode()
    ids = set(__import__("re").findall(r'SubResource\("([^"]+)"\)', t))
    defs = set(__import__("re").findall(r'\[sub_resource type="[^"]+" id="([^"]+)"\]', t))
    assert ids <= defs


def test_hit_vfx_event_joins_frame_data_events(tmp_path: Path):
    from studio_core.export import hitfx, unity

    src = _source(tmp_path)
    patched = dict(exp.patch_clips(unity.export(src), src))
    slash = next(v for r, v in patched.items() if r.startswith("Animations/slash_") and r.endswith(".anim")).decode()
    hit = ("  m_Events:\n  - time: 0.3\n    functionName: OnHit\n    data: vfx|0|0\n"
           "    objectReferenceParameter: {fileID: 0}\n    floatParameter: 1\n    intParameter: 3\n    messageOptions: 0\n")
    both = hitfx._add_event(slash, hit)
    assert both.count("functionName: OnFrameEvent") == 3 and both.count("functionName: OnHit") == 1
    assert both.count("m_Events:") == 1 and both.endswith("messageOptions: 0\n")
    plain = hitfx._add_event("  m_Events: []\n", hit)
    assert plain == hit
