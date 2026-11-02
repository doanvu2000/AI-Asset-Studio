from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from studio_core.engine.flows import FlowLibrary, flow_deps, parse_source, validate_def
from studio_core.engine.library import RecipeLibrary

CORE = Path(__file__).resolve().parents[1] / "studio_core"
NEW = ["character-pack", "level-pack", "ui-pack", "game-slice"]


@pytest.fixture(scope="module")
def libs(tmp_path_factory):
    user = tmp_path_factory.mktemp("user")
    return RecipeLibrary(CORE / "recipes", user / "r"), FlowLibrary(CORE / "flows", user / "f")


def test_builtin_templates_present(libs):
    _, flows = libs
    for fid in NEW:
        assert fid in flows.flows


@pytest.mark.parametrize("fid", NEW)
def test_template_valid(libs, fid):
    recipes, flows = libs
    d = flows.flows[fid]
    assert len(d.nodes) <= 24
    errors = [i for i in validate_def(d, recipes.get) if i.get("level", "error") == "error"]
    assert not errors, errors


@pytest.mark.parametrize("fid", NEW)
def test_map_outputs_exist(libs, fid):
    recipes, flows = libs
    d = flows.flows[fid]
    by_id = {n.id: n for n in d.nodes}
    for n in d.nodes:
        for src in n.map.values():
            ref, kind, name, _ = parse_source(src)
            if ref == "workflow":
                assert name in d.inputs
            elif kind == "outputs":
                assert name in recipes.get(by_id[ref].recipe_id).outputs


def test_level_pack_character_is_optional_and_reaches_scene(libs):
    _, flows = libs
    d = flows.flows["level-pack"]
    assert d.inputs["character"].required is False
    scene = next(n for n in d.nodes if n.id == "scene")
    assert scene.map["character"] == "workflow.inputs.character"


@pytest.mark.parametrize("fid", ["level-pack", "ui-pack"])
def test_style_audit_node_is_optional_and_blocks_nothing(libs, fid):
    _, flows = libs
    d = flows.flows[fid]
    style = next(n for n in d.nodes if n.recipe_id == "style-lock")
    assert style.inputs == {"review": False}
    deps = {parse_source(s)[0] for s in style.map.values()}
    assert deps and deps <= {n.id for n in d.nodes if n.id != style.id}
    for n in d.nodes:
        assert style.id not in {parse_source(s)[0] for s in n.map.values()}


def test_game_slice_reuses_concept_and_sprite(libs):
    _, flows = libs
    d = flows.flows["game-slice"]
    by_id = {n.id: n for n in d.nodes}
    assert [n.recipe_id for n in d.nodes].count("concept-2d") == 1
    assert [n.recipe_id for n in d.nodes].count("sprite-8dir-2d") == 1
    assert by_id["scene"].map["character"] == "sprite.outputs.sheet[0]"
    assert by_id["kit"].map["image"] == "concept.outputs.final[0]"
    assert by_id["minimap"].map["source"] == "scene.outputs.tmx[0]"
    style = by_id["style"]
    assert style.inputs == {"review": False}
    assert not any("style" in {parse_source(s)[0] for s in n.map.values()} for n in d.nodes)


@pytest.mark.parametrize("fid", NEW)
def test_every_node_gets_scheduled(libs, fid):
    _, flows = libs
    deps = flow_deps(flows.flows[fid])
    done: set[str] = set()
    while len(done) < len(deps):
        ready = [n for n, d in deps.items() if n not in done and set(d) <= done]
        assert ready, f"stuck: {set(deps) - done}"
        done.update(ready)
