from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw

from studio_core.adapters.ui_layout import ClaudeUiLayoutExecutor
from studio_core.contracts import Lane, RunStatus, StepResult
from studio_core.ops import ui_screen as us
from studio_core.ops.executors import TrimPadResize
from test_engine_runner import Env
from test_ui_screen import RECIPES, FakeQuery

MINI = """id: ui-icons-mini
name: mini
inputs:
  icons: {type: file, ext: [json], required: false}
  icon_source: {type: enum, default: zimage, options: [zimage, placeholder]}
steps:
  - id: layout
    kind: claude.ui_layout
    in: {icons: inputs.icons}
    params: {screen: home, planner: template, icon_style: "candy pastel", icon_source: "${inputs.icon_source}"}
  - id: icon_gen
    kind: comfy.t2i_zimage_turbo
    params:
      "57:27.text": ["${steps.layout.json.icon_prompts}"]
    map_param: "57:27.text"
  - id: icon_rmbg
    kind: comfy.remove_bg_birefnet
    in: {image: icon_gen.images}
    map: image
  - id: icon_fit
    kind: op.trim_pad_resize
    in: {images: icon_rmbg.images}
    params: {size: 128, pad: 0.04, optional: true}
  - id: screen
    kind: op.ui_screen
    in: {layout: layout.json, icons: inputs.icons, gen_icons: icon_fit.images}
    params: {screen: home}
outputs:
  screen: screen.screen
"""


class FakeComfy:
    def __init__(self, kind: str) -> None:
        self.kind, self.lane, self.version, self.prompts = kind, Lane.GPU, "1", []

    def cacheable(self, params: dict[str, Any]) -> bool:
        return False

    def cache_material(self, params: dict[str, Any]) -> str:
        return ""

    async def run(self, ctx: Any, params: dict[str, Any], inputs: Any) -> StepResult:
        img = Image.new("RGBA", (256, 256), (255, 255, 255, 255 if self.kind.startswith("comfy.t2i") else 0))
        ImageDraw.Draw(img).ellipse((40, 40, 216, 216), fill=(200, 60, 120, 255))
        self.prompts.append(params.get("57:27.text"))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return StepResult(outputs={"images": [await ctx.blobs.put_bytes(buf.getvalue(), name="x.png",
                                                                        mime="image/png")]})


def test_icons_to_draw_skips_pack_names() -> None:
    lay = us.normalize_layout(us.template("home", "X"), "home")
    names = us.icon_names(lay)
    assert names and len(names) == len(set(names))
    todo, prompts = us.icons_to_draw(lay, "candy", ["icon_settings", "shop"])
    assert "settings" not in todo and "shop" not in todo and len(todo) == len(prompts)
    assert all("white background" in p and "candy" in p for p in prompts)
    assert "gear" in us.icon_prompt("settings", "x")


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["zimage", "placeholder"])
async def test_generated_icons_replace_placeholders(tmp_path: Path, source: str) -> None:
    env = Env(tmp_path, [], {"ui-icons-mini": MINI})
    env.registry.register(ClaudeUiLayoutExecutor(env.settings, FakeQuery({})))
    gen, rmbg = FakeComfy("comfy.t2i_zimage_turbo"), FakeComfy("comfy.remove_bg_birefnet")
    for e in (gen, rmbg, TrimPadResize(), *(c() for c in us.UI_SCREEN_OPS)):
        env.registry.register(e)
    run_id = await env.engine.start_run("ui-icons-mini", {"icon_source": source})
    await asyncio.wait_for(env.engine.wait_idle(run_id), 60)
    steps = env.steps(run_id)
    assert env.status(run_id) == RunStatus.SUCCEEDED, {k: s.error for k, s in steps.items() if s.error}
    layout = json.loads(env.blobs.path(steps["layout"].outputs["json"][0].hash).read_bytes())
    screen = json.loads(env.blobs.path(steps["screen"].outputs["screen"][0].hash).read_bytes())
    if source == "zimage":
        assert layout["icon_names"] and len(gen.prompts) == len(layout["icon_names"])
        assert screen["placeholder_icons"] == []
    else:
        assert layout["icon_prompts"] == [] and gen.prompts == []
        assert screen["placeholder_icons"]


def test_prompt_recipe_wires_icon_steps() -> None:
    text = (RECIPES / "ui-screen-prompt.yaml").read_text(encoding="utf-8")
    assert "${steps.layout.json.icon_prompts}" in text and "gen_icons: icon_fit.images" in text


def test_tmp_converter_script_is_per_asset_and_reflection_only() -> None:
    from studio_core.export.ui_screen import tmp_cs

    cs = tmp_cs("Pause Menu")
    assert "namespace AssetStudio\n" in cs and "class UiTmp_Pause_Menu : AssetPostprocessor" in cs
    assert 'const string Asset = "Pause Menu";' in cs and "public static void BuildAll()" in cs
    assert "using TMPro" not in cs and "Replace('" + "\\" * 2 + "', '/')" in cs
