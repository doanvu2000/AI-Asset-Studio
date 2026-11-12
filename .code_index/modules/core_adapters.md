# core_adapters — executor ngoài, resource, bootstrap, hardware

Gốc `core/studio_core/adapters/`, `bootstrap/`, `hardware.py`. Adapter nói chuyện với process/dịch vụ ngoài.

## Đăng ký
- `adapters/__init__.py::register` — tạo `ComfyResource`, `BlenderResource`, `CodexResource`, `ClaudeResource`; `register_prefix("comfy.", ...)` + đăng ký từng executor; trả `list[Resource]` cho `build_app`.
- `contracts.py::Resource` — `name`, `health`, `ensure_started`, `stop`; engine gọi `_ensure_resource` trước khi chạy step cần resource.

## ComfyUI (Lane.GPU)
- `adapters/comfy/server.py::ComfyResource` — spawn/quản lý process ComfyUI (`name="comfyui"`), watchdog, crash listener, `free()` nhả model; `adapters/_winproc.py::JobObject` giết cây process cùng core.
- `adapters/comfy/client.py::ComfyClient` — HTTP + WS tới ComfyUI: queue prompt, preview frame (`parse_preview_frame`), `history_files`, `execution_error`.
- `adapters/comfy/executor.py::ComfyExecutor` — kind `comfy.<workflow>`; `build_prompt` (ghi param vào workflow json), seed (`SEED_MAX` 2^53), progress.
- `adapters/comfy/views.py::CheckedViewsExecutor` (multi-view có kiểm), `mesh_check.py::CheckedMeshExecutor`, `retexture.py::RetexPaintExecutor`, `upscale.py::UpscaleModelExecutor` — executor bọc `registry.resolve`.

## Blender (Lane.BLENDER)
- `adapters/blender/client.py::BlenderResource` — chạy Blender background + `blender/worker.py`, giao tiếp JSON; `ensure_started`, `call(cmd, params)`, `BOOT_TIMEOUT_S` 90s.
- `adapters/blender/executor.py::BlenderRenderDirsExecutor` · `BlenderAnimateDirsExecutor` · `BlenderGameReadyExecutor` · `BlenderCleanupExecutor` · `BlenderScriptExecutor` (+ `call_script`, `SCRIPT_ERRORS`).
- File theo cặp `*_bpy.py` (chạy trong Blender, có `bpy`) và `*_math.py` (thuần numpy, test được): `lod`, `fracture`, `modular`, `retex`, `rig3d`, `icon3d`, `paperdoll`; `blender/agent.py` + `procedural.py` = Procedural 3D (Claude viết code Blender).
- Script chạy trong Blender nằm ở `paths.py::blender_scripts_dir`.

## Claude / Codex (Lane.CLOUD)
- `adapters/claude.py::ClaudeTaskExecutor` (`claude.task`) · `ClaudeBriefExecutor` (`claude.brief`) · `ClaudeResource` · `ask` · `build_options` · `MODEL_IDS` (opus/sonnet/haiku, `DEFAULT_MODEL` opus) · `named_schema` (structured output).
- Executor Claude chuyên biệt: `adapters/localize.py::ClaudeLocalizeExecutor`, `npc.py::ClaudeNpcDialogueExecutor`, `card_list.py::ClaudeCardListExecutor`, `terrain.py::ClaudeTerrainPlanExecutor`, `storyboard.py::ClaudeStoryboardExecutor`, `ui_layout.py::ClaudeUiLayoutExecutor`, `spine_review.py::SpineReviewLoopExecutor`.
- `adapters/codex.py::CodexImageExecutor` (`codex.image`) · `CodexResource` — sinh ảnh qua Codex CLI.
- `adapters/gdd.py::ManifestPlanner` · `adapters/gdd_plan.py::GddPlanner` — Claude lập kế hoạch asset từ GDD.
- `adapters/translate.py::Translator` — dịch prompt (route `/translate`).
- `adapters/embedder.py::ClipEmbedder` — CLIP embed cho `ImageIndex`; `adapters/normal_ai.py::NormalAi` — MoGe-2 normal map (worker process CPU); `adapters/tts.py::SherpaTts` — giọng NPC.
- `adapters/style_bible.py::StyleBibleWriter`, `adapters/project_qa.py::ProjectQaReviewer` — Claude vision viết style bible / soi QA project.

## Bootstrap (cài tự động)
- `bootstrap/manifest.json` — gói model (pack), file, URL, sha256, custom node ComfyUI.
- `bootstrap/plan.py::describe` · `pack_models` · `recipe_packs` · `comfy_ready` — tính trạng thái cài.
- `bootstrap/job.py::SetupJob` — tải model (`download`, resume, `MODEL_THREADS` 2, `file_sha256`), giải nén (`extract_archive`), `Cancelled`, `SetupError`.

## Hardware fit
- `hardware.py::probe` · `detect_gpu_info` · `read_ram` · `tier_of` — đèn đỏ/vàng/xanh; `fit_needs` · `fit_all` · `recipe_needs` · `recommend`.
- `scan_jobs.py::ScanJobs` — job quét nền có huỷ (`ScanCancelled`), dùng cho audit/QA scan.

## Invariant
- Adapter chỉ phụ thuộc `contracts` + `config`, không import `api/`.
- Lời gọi ngoài có timeout (Claude `DEFAULT_TIMEOUT_S` 300s; Blender boot 90s); process con gắn Job Object để không mồ côi khi core thoát.
- Test: `core/tests/test_adapters_comfy.py`, `test_adapters_fakes.py`, `test_adapters_cloud.py`, `test_p4_blender.py`, `test_setup.py`, `test_hardware_fit.py`; `test_adapters_live.py` cần `-m live`.
