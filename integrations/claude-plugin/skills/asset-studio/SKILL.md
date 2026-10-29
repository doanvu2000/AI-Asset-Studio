---
name: asset-studio
description: Make game assets with the local AI Asset Studio (sprites, 8-direction sheets, Spine characters, 3D props, terrain, skyboxes, UI kits, VFX, tilesets, icons, packs) through its MCP tools, and put them into Unity/Godot/Blender projects. Use when the user asks to generate, edit, restyle, import, review or package game art, or to drive AI Asset Studio.
---

# AI Asset Studio

Local app + Python core that runs **recipes** (asset pipelines on ComfyUI GPU, Blender, Claude or CPU). You drive it
through the `asset-studio` MCP server; everything you do also shows live in the desktop app (agent panel, run flow).
No MCP? Same actions via `studio-core agent …` (see bottom).

## First moves
1. `system_status` — core/ComfyUI/Blender up, lanes busy, missing model packs, Unity/Godot preview ready.
2. Pick a recipe: `references/recipes.md` (or MCP resource `asset-studio://recipes`), then `describe_recipe(id)`.
3. `list_projects` → reuse the game's project, or `create_project(name, prompt_suffix=…, reference_image=…)` to lock
   a style. Always pass `project_id` so assets match.

## Running
- Simple: `generate(recipe, inputs, project_id)` = start + wait + auto-pick first candidate + export outputs.
- Controlled: `start_run(..., gate_policy)` → `wait_run` (loop on `timed_out`) → gates → `get_outputs`.
  - `human` (default): user picks in the app; tell them and keep waiting.
  - `agent`: you inspect candidates (thumbnails, `review.score`) and call `resolve_gate(step_id, pick=["2"])` or
    `approve=true`.
  - `auto_first`: wait_run resolves gates itself.
- File inputs = **absolute paths** (or blob hashes from earlier outputs). Text prompts in English.
- GPU/Blender recipes take 1–15 min and run one GPU job at a time; a step in `waiting_resource` is queued, not stuck
  (check `system_status.lanes`). CPU/Claude recipes finish in seconds.
- Failure: read the step `error`, fix inputs, `retry_step(step_id)`. `cancel_run` to stop.
- Results land in the Library as draft versions; `set_version_status(approved)` what the user keeps.

## Chaining ("Dùng tiếp cho…")
`follow_up_options(run_id)` lists recipes the output can feed with file inputs prefilled — pass its `inputs` to
`start_run`. Character kit: `character_kit` gives `then=[…]` so the core chains 8-dir → Spine by itself.

Typical flows:
- **2D character**: `concept-2d` → follow-up `sprite-8dir-2d` (+ `then` → `spine-character-image`) →
  `sprite-anim-video` (action idle/walk/run/…) → `build_asset_pack`.
- **3D scene**: terrain (`terrain-3d` if listed) + `skybox-360` + props `prop-3d` (→ `prop-destructible`,
  `mesh-retexture`) + `pbr-material`/`texture-seamless`.
- **UI / icons / VFX**: `ui-kit-style`, `icon-pack`, `item-rarity`, `vfx-flipbook`, `bitmap-font`.
- **Existing game reskin**: `restyle_scan(folder)` → `start_run("restyle-folder", {folder, style, sample: 6})`
  → review → `start_run` again with `sample: 0` → `restyle_apply(run_id)` (backup zip; `restyle_undo`).
- **Own files**: `import_to_library(paths, project_id)` then use them as inputs / in packs.

## Into the engine / DCC
- `export_to_engine(engine="unity"|"godot", run_id=…, dest_dir=<project>/Assets/Art)` copies a ready folder
  (sliced sprites, anims, prefab/scene builders). `engine_preview(step_id)` opens it headless and returns import
  errors + a screenshot.
- Unity Editor open? After export use the `unity-editor-mcp` tools (refresh AssetDatabase, open/instantiate the
  prefab, check console). Use a test project when the user's Editor is busy.
- Blender MCP: `get_outputs` gives GLB paths → `execute_blender_code` to import, `look` to inspect topology/scale
  before shipping a prop; send fixes back via `mesh-retexture` or `import_to_library`.
- Ship: `project_qa` (consistency) → `project_qa_fix` → `build_style_bible` → `build_asset_pack(engines, atlas,
  mobile)`.

## Rules
- Do not choose at `human` gates unless the user said so. Don't start several GPU recipes at once "to be fast".
- Report output paths (`get_outputs`), not blob hashes, to the user.
- Restyle `apply` overwrites a game folder: only after the user approved the dry run.

## CLI fallback
`studio-core agent [--json] recipes | describe <id> | status | run <recipe> -i name=value … [--project id]
[--gate auto_first] --wait | wait <run> | outputs <run>` (dev: `cd core && uv run python -m studio_core agent …`).
