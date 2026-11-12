# core_ops_recipes — op.* executor, recipe YAML, workflow json

Gốc `core/studio_core/ops/`, `recipes/`, `flows/`, `workflows/`. `op.*` = xử lý CPU thuần (PIL/numpy/ffmpeg), `Lane.CPU`.

## Khung op
- `ops/executors.py::OpExecutor` — base: `kind`, `lane`, `version`, `cacheable`, `run`; `PerImageOp` chạy từng ảnh trong input.
- `ops/__init__.py::register` — đăng ký toàn bộ op (`ALL_OPS`, `RIG_OPS`, `PIXEL_OPS`, `FX_OPS`, `SPINE_REF_OPS`, `VARIANT_OPS`, ...); `Sheet` nhận `PresetStore`; `VideoFrames`/`AudioPost`/`Animatic`/`KeyartVideo` nhận `settings` (ffmpeg).
- Hầu hết file trong `ops/` = 1 nhóm kind (`op.<tên>`); đọc file tương ứng, không suy từ tên.

## Op chính (file → class)
- Ảnh cơ bản: `ops/executors.py::TrimPadResize` (`op.trim_pad_resize`), `Downscale`, `Pixelate`, `Normalize`, `MirrorDirs`, `Sheet` (`op.sheet`, sprite sheet + `ExportPreset`), `GlbValidate`, `ViewCheck`.
- Tile/texture: `ops/tile.py::Seamless` · `TileWang` · `TileResize` · `TileNormal` · `TilePixel` · `TileCheck`; `ops/autotile.py::Autotile`; `ops/terrain.py::TerrainPack`; `ops/tile_anim.py::TileAnimate`; `ops/iso.py::IsoTiles`; `ops/ground_strip.py::GroundStrip`.
- Pixel art: `ops/pixel.py::PixelArt` · `PixelPlan`; `ops/pixel_cleanup.py::PixelCleanup`; `ops/hue_variants.py::HueVariants`.
- Sprite/anim: `ops/fx.py::SpriteFx` · `FxParams`; `ops/anim_stabilize.py::AnimStabilize`; `ops/anim_video.py::AnimPrep`; `ops/shadow_outline.py::ShadowOutline`; `ops/frame_data.py::FrameData`; `ops/damage.py::DamageStates`; `ops/enemy_variants.py::EnemyVariants`; `ops/item_fx.py::ItemSpin`.
- Spine/rig: `ops/rig.py::SpineExport` · `Parts` · `Layers`; `ops/parts.py::Partition`; `ops/spine.py::Bone`; `ops/spine_face.py::SpineFacePrep`; `ops/spine_physics.py::SpinePhysics`; `ops/spine_convert.py::SpineConvert`; `ops/spine_runtime.py::resolve_skins`.
- UI/store: `ops/ui.py::NineSlice` · `UiSplit`; `ops/ui_screen.py::UiScreen`; `ops/card_set.py::CardSet`; `ops/bitmap_font.py::BitmapFont`; `ops/store_kit.py::StoreKit`; `ops/minimap.py::MinimapFinish`; `ops/npc.py::NpcPack`; `ops/portrait.py::PortraitSet`.
- 3D/PBR: `ops/pbr.py::Pbr`; `ops/normal2d.py::Normal2D`; `ops/glb.py::GlbError`; `ops/icon3d.py::IconCompose`; `ops/pano.py::PanoCubemap` · `PanoFix`; `ops/lut.py::FitLut`; `ops/room_kit.py::RoomKit`; `ops/paperdoll.py::Paperdoll`.
- Media: `ops/audio.py::AudioPost` (ffmpeg); `ops/soundscape.py::SoundscapeMix`; `ops/vfx.py::VfxFrames`; `ops/vfx_particles.py::VfxParticles`; `ops/weather.py::WeatherOverlay`; `ops/keyart.py::KeyartVideo`; `ops/animatic.py::Animatic`.
- Project-level: `ops/restyle.py::ApplyRestyle` · `ScanProject`; `ops/style_audit.py::StyleAudit`; `ops/image_layers.py::ImageLayers`; `ops/localize.py::LocalizeRender`; `ops/playable_scene.py::PlayableScene`; `ops/upscale.py::UpscalePost`.
- `ops/presets.py::PresetStore` · `ExportPreset` · `apply_preset` — preset sheet (hướng, tên clip) lưu JSON, dùng bởi `Sheet` và export.

## Recipe YAML (`recipes/*.yaml`)
- Khung: `id`, `name`, `preview`, `description`, `inputs` (type/default/min/max/options), `steps` (`id`, `kind`, `in: {port: step.output}`, `params`, `map`), `outputs`. Ví dụ `recipes/concept-2d.yaml`: `comfy.t2i_zimage_turbo` → `gate.pick` → `comfy.remove_bg_birefnet` → `op.trim_pad_resize`.
- Prefix kind: `comfy.*` (workflow json), `blender.*`, `claude.*`, `codex.*`, `op.*`, `gate.*` (xem `engine/kinds.py::describe_kinds`).
- Biến thể cùng chủ đề đặt hậu tố (`-prompt`, `-brief`, `-style`, `-codex`, `-image`, `-mv`); thêm recipe = thêm file YAML, `RecipeLibrary` tự nạp lại khi thư mục đổi (`maybe_reload`).
- Ảnh preview recipe: `recipes/previews/*.webp`; thumbnail trong app: `app/src/renderer/src/assets/art/recipe-*.webp`.
- Catalog markdown sinh bằng `mcp/catalog.py::catalog_markdown` → `integrations/*/skills/asset-studio/references/recipes.md` (qua `scripts/gen_agent_docs.py`).

## Flow mẫu + ComfyUI workflow
- `flows/*.yaml` (`character-pack`, `concept-to-sprite`, `game-slice`, `level-pack`, `ui-pack`) — graph recipe builtin, nạp bởi `engine/flows.py::FlowLibrary`.
- `workflows/*.api.json` + `workflows/index.json` — workflow ComfyUI dạng API; `index.json` map tên param → `node.field`; `adapters/comfy/executor.py::build_prompt` ghi param vào graph. Kind = `comfy.<tên file bỏ .api.json>`.

## Invariant
- Input/output của op đều là `BlobRef` qua `StepContext`; op không giữ trạng thái giữa các lần chạy.
- Đổi hành vi op → bump `version` của executor để vô hiệu cache cũ.
- Test: `core/tests/test_ops_executors.py`, `test_ops_image.py`, `test_p2_recipes.py`, `test_p7_kinds.py`, và `test_<tên op>.py` tương ứng (vd `test_autotile.py`, `test_pbr.py`).
