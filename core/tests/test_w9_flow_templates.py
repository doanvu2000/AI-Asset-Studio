from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from studio_core.engine.flows import FlowLibrary, parse_source, validate_def
from studio_core.engine.library import RecipeLibrary

CORE = Path(__file__).resolve().parents[1] / "studio_core"
NEW = ["character-pack", "level-pack", "ui-pack"]


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
