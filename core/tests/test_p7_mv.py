from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from studio_core.config import Settings
from studio_core.engine.recipe import Binding, ConcatBinding, load_recipes

WORKFLOWS_DIR: Path = Settings.model_fields["comfy_workflows_dir"].default
needs_workflows = pytest.mark.skipif(not (WORKFLOWS_DIR / "index.json").is_file(),
                                     reason="ComfyUI workflows not installed")
VIEW_ORDER = ["front", "left", "back", "right"]


def _mv_recipe() -> Any:
    return load_recipes(Settings().recipes_dir)["sprite-8dir-3d-mv"]


def test_mv_recipe_shape() -> None:
    r = _mv_recipe()
    assert [s.id for s in r.steps] == ["concept", "pick", "views", "check", "mesh", "cleanup", "render", "dirs", "ndirs",
                                       "down", "ndown", "fx", "nfx", "sheet", "nsheet"]
    assert r.step("views").kind == "views.qwen_checked"
    assert r.step("check").kind == "gate.approve"
    assert r.resolve_params(r.step("check"), r.coerce_inputs({"prompt": "x"}))["enabled"] is False
    assert r.resolve_params(r.step("check"), r.coerce_inputs({"prompt": "x", "review_views": True}))["enabled"] is True
    assert str(r.step("check").bindings["images"]) == "views.images"
    mesh = r.step("mesh")
    assert mesh.kind == "mesh.checked" and mesh.params["workflow"] == "img2mesh_trellis2_mv_textured"
    image = mesh.bindings["image"]
    assert isinstance(image, ConcatBinding)
    assert [str(b) for b in image.parts] == ["pick.images", "check.images"]
    vals = r.coerce_inputs({"prompt": "wizard", "faces": 20000})
    params = r.resolve_params(mesh, vals)
    assert params["186.target_face_count"] == 20000
    assert params["94.target_resolution"] == 1024 and params["241.resolution"] == 512
    assert params["_restart_before"] is True and params["_timeout_s"] == 900
    assert params["_drop_inputs"] == "${steps.views.report.drop_inputs}" and "views" in mesh.deps
    assert isinstance(r.step("views").bindings["image"], Binding)
    assert r.step("views").bindings["image"].source == "pick"
    assert str(r.outputs["final"]) == "sheet.sheet"


def test_mv_recipe_kinds_resolve(tmp_path: Path) -> None:
    from studio_core.adapters.blender import BlenderCleanupExecutor, BlenderRenderDirsExecutor
    from studio_core.adapters.comfy import CheckedMeshExecutor, CheckedViewsExecutor
    from studio_core.contracts import ExecutorRegistry, Lane
    from studio_core.engine.gates import register_gates
    from studio_core.ops import register as register_ops

    reg = ExecutorRegistry()
    register_ops(reg)
    register_gates(reg)
    settings = Settings(data_dir=tmp_path)
    reg.register(BlenderRenderDirsExecutor(settings, object()))  # type: ignore[arg-type]
    reg.register(BlenderCleanupExecutor(settings, object()))  # type: ignore[arg-type]
    reg.register_prefix("comfy.", lambda kind: type("C", (), {"kind": kind, "lane": Lane.GPU})())  # type: ignore[arg-type, return-value]
    reg.register(CheckedViewsExecutor(reg.resolve))
    reg.register(CheckedMeshExecutor(reg.resolve))
    for s in _mv_recipe().steps:
        reg.resolve(s.kind)


def test_mv_tail_matches_single_view_recipe() -> None:
    recipes = load_recipes(Settings().recipes_dir)
    mv, sv = recipes["sprite-8dir-3d-mv"], recipes["sprite-8dir-3d"]
    for sid in ("cleanup", "render", "dirs", "down", "fx", "nfx", "sheet", "ndirs", "ndown", "nsheet"):
        assert mv.step(sid).kind == sv.step(sid).kind
        assert mv.step(sid).params == sv.step(sid).params
    assert mv.inputs.keys() - sv.inputs.keys() == {"review_views", "consistency_check"}


@needs_workflows
def test_mv_workflow_binds_views_in_rig_order() -> None:
    from studio_core.adapters.comfy.executor import build_prompt, load_index

    entry = load_index(WORKFLOWS_DIR)["img2mesh_trellis2_mv_textured"]
    wf = json.loads((WORKFLOWS_DIR / entry["file"]).read_text(encoding="utf-8"))
    cond = next(n for n in wf.values() if n["class_type"] == "Pixal3DMultiViewConditioning")
    assert cond["inputs"]["fov"] == 20.0
    # The single-view Pixal3D UNet accepts multi-view conditioning but returns a flat, untextured slab.
    unets = {n["inputs"]["unet_name"] for n in wf.values() if n["class_type"] == "UNETLoader"}
    assert "pixal3d_multiview_int8_convrot.safetensors" in unets
    assert "pixal3d_int8_convrot.safetensors" not in unets

    def load_node_of(view: str) -> str:
        crop = wf[cond["inputs"][view][0]]
        assert crop["class_type"] == "ImageCropToMask" and crop["inputs"]["pad_factor"] == 1.1
        return crop["inputs"]["images"][0]

    keys = [i["key"] for i in entry["images"]]
    assert keys == [f"{load_node_of(v)}.image" for v in VIEW_ORDER]
    azimuths = _mv_recipe().step("mesh").params["azimuths"]
    assert [azimuths[k] for k in keys] == [0, 90, 180, 270]

    names = [f"studio/{v}.png" for v in VIEW_ORDER]
    prompt, _ = build_prompt(wf, entry, {"94.target_resolution": 1024}, names, "p")
    for key, name in zip(keys, names):
        assert prompt[key.split(".")[0]]["inputs"]["image"] == name
    for node in prompt.values():
        for v in node["inputs"].values():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str):
                assert v[0] in prompt, v
    assert not any(n["class_type"] in ("Pixal3DConditioning", "MoGeInference") for n in prompt.values())


@needs_workflows
def test_mv_qwen_views_are_left_back_right() -> None:
    from studio_core.adapters.comfy.executor import load_index

    entry = load_index(WORKFLOWS_DIR)["multi_angles_qwen2511"]
    wf = json.loads((WORKFLOWS_DIR / entry["file"]).read_text(encoding="utf-8"))
    views = _mv_recipe().step("views").params["views"]
    assert [v["name"] for v in views] == ["left", "back", "right"]
    outputs = [o["node"] for o in entry["outputs"]]
    assert [outputs.index(v["output"]) for v in views] == sorted(outputs.index(v["output"]) for v in views)
    for v, want in zip(views, ["90 degrees to the right", "180 degrees", "90 degrees to the left"]):
        branch = wf[v["output"]]["inputs"]["images"][0].split(":")[1]
        assert v["prompt_key"].split(":")[1] == branch
        assert v["prompt_key"].split(".")[0] in wf
        assert want in v["prompt"]
        assert v["slot"] == f"330.{v['name']}"


@needs_workflows
def test_mv_workflow_drops_a_view_slot() -> None:
    from studio_core.adapters.comfy.executor import build_prompt, load_index

    entry = load_index(WORKFLOWS_DIR)["img2mesh_trellis2_mv_textured"]
    wf = json.loads((WORKFLOWS_DIR / entry["file"]).read_text(encoding="utf-8"))
    names = ["studio/front.png", "studio/back.png", "studio/right.png"]
    prompt, values = build_prompt(wf, entry, {"_drop_inputs": ["330.left"]}, names, "p")
    assert "left" not in prompt["330"]["inputs"]
    assert prompt["122"]["inputs"]["image"] == names[0]
    assert prompt["402"]["inputs"]["image"] == names[1] and prompt["403"]["inputs"]["image"] == names[2]
    assert "401.image" not in values


@needs_workflows
def test_mesh_check_azimuths_follow_dropped_slot() -> None:
    from studio_core.adapters.comfy import CheckedMeshExecutor
    from studio_core.adapters.comfy.executor import load_index

    gen: Any = type("G", (), {"entry": load_index(WORKFLOWS_DIR)["img2mesh_trellis2_mv_textured"],
                              "workflow_path": WORKFLOWS_DIR / "img2mesh_trellis2_mv_textured.api.json"})()
    params = {**_mv_recipe().step("mesh").params, "_drop_inputs": ["330.left"]}
    assert CheckedMeshExecutor(lambda k: gen)._image_azimuths(gen, params, 3) == [0, 180, 270]
