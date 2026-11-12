# Lookup — task → file

Đọc file này ĐẦU TIÊN mỗi phiên (≤ 80 dòng). Mỗi dòng: 1 entry dạng `path::Symbol` (script
`index-check` kiểm được). Không ghi số dòng. Không văn xuôi.

| Task / màn | Entry | Liên quan | Không đụng / invariant | Bug liên quan |
|---|---|---|---|---|
| Khởi động core, build app | `core/studio_core/app.py::build_app` | `core/studio_core/main.py::main`, `core/studio_core/api/__init__.py::mount_api` | token Bearer trên mọi route `/api` | — |
| Lock file / chặn chạy đôi | `core/studio_core/mcp/lockfile.py::SpawnLock` | `core/studio_core/mcp/link.py::CoreLink` | `core.lock.json` = nguồn attach duy nhất | — |
| Kiểu dữ liệu chung (Run/Step/Lane) | `core/studio_core/contracts.py::Lane` | `core/studio_core/contracts.py::ExecutorRegistry` | đổi enum = đổi DB + app `types.ts` | — |
| Start run / vòng đời run | `core/studio_core/engine/runner.py::StudioEngine` | `core/studio_core/api/routes.py::create_run` | gate không chiếm lane | — |
| Chạy 1 step, cache hit | `core/studio_core/engine/runner.py::_exec_step` | `core/studio_core/engine/cache.py::cache_key` | blob còn tồn tại mới tính hit | — |
| Scheduler lane / ưu tiên | `core/studio_core/engine/scheduler.py::LaneScheduler` | `core/studio_core/engine/gpu.py::GpuControl` | affinity thua waiter quá 120s | — |
| Yield / resume GPU | `core/studio_core/engine/gpu.py::GpuControl` | `core/studio_core/api/gpu.py::yield_gpu` | trạng thái yield chỉ trong RAM | — |
| Gate pick / approve | `core/studio_core/engine/gates.py::PickGate` | `core/studio_core/engine/gates.py::ApproveGate`, `core/studio_core/engine/autopick.py::pick_by_qa` | gate không cacheable | — |
| Parse / validate recipe | `core/studio_core/engine/recipe.py::Recipe` | `core/studio_core/engine/library.py::RecipeLibrary` | — | — |
| Schema param từng kind | `core/studio_core/engine/kinds.py::describe_kinds` | `core/studio_core/engine/library.py::lint` | — | — |
| Cache key / seed | `core/studio_core/engine/cache.py::executor_cache_key` | `core/studio_core/engine/seeds.py::pin_overrides` | param điều khiển không vào key | — |
| Flow (workflow) chạy graph recipe | `core/studio_core/engine/flows.py::FlowEngine` | `core/studio_core/engine/flows.py::validate_def`, `core/studio_core/engine/flow_batch.py::FlowBatches` | trạng thái node suy từ run con | — |
| Style bible / style project | `core/studio_core/engine/style_bible.py::StyleBible` | `core/studio_core/engine/projects.py::apply_style` | — | — |
| QA ảnh / project | `core/studio_core/engine/qa.py::score_image` | `core/studio_core/engine/project_qa.py::measure` | — | — |
| Trùng / tương tự asset | `core/studio_core/engine/similar.py::duplicate_groups` | `core/studio_core/engine/image_index.py::ImageIndex` | — | — |
| Tự export khi duyệt | `core/studio_core/engine/auto_export.py::AutoExporter` | `core/studio_core/store/bundle.py::export_engine` | — | — |
| Executor ComfyUI | `core/studio_core/adapters/comfy/executor.py::ComfyExecutor` | `core/studio_core/adapters/comfy/server.py::ComfyResource`, `core/studio_core/adapters/comfy/client.py::ComfyClient` | workflow ở `workflows/*.api.json` | — |
| Executor Blender | `core/studio_core/adapters/blender/executor.py::BlenderGameReadyExecutor` | `core/studio_core/adapters/blender/client.py::BlenderResource` | `*_bpy.py` chạy trong Blender | — |
| Executor Claude / Codex | `core/studio_core/adapters/claude.py::ClaudeTaskExecutor` | `core/studio_core/adapters/codex.py::CodexImageExecutor` | — | — |
| Đăng ký executor ngoài | `core/studio_core/adapters/__init__.py::register` | `core/studio_core/ops/__init__.py::register` | — | — |
| Op CPU cơ bản / sprite sheet | `core/studio_core/ops/executors.py::Sheet` | `core/studio_core/ops/executors.py::OpExecutor`, `core/studio_core/ops/presets.py::PresetStore` | đổi hành vi = bump `version` | — |
| Spine / rig | `core/studio_core/ops/rig.py::SpineExport` | `core/studio_core/ops/spine_face.py::SpineFacePrep` | — | — |
| Cài model tự động | `core/studio_core/bootstrap/job.py::SetupJob` | `core/studio_core/bootstrap/plan.py::describe` | sha256 + resume | — |
| Export step ra engine | `core/studio_core/store/bundle.py::export_engine` | `core/studio_core/api/routes.py::export_step`, `core/studio_core/export/source.py::Source` | ghi dưới thư mục đích | — |
| Export Flutter / Flame | `core/studio_core/export/flutter.py::export` | `core/studio_core/export/flutter_demo.py` | đích mặc định | — |
| Export Android / iOS | `core/studio_core/export/android.py::export` | `core/studio_core/export/ios.py::export` | — | — |
| Export Unity / Godot | `core/studio_core/export/unity.py::export` | `core/studio_core/export/godot.py::export` | GUID Unity ổn định | — |
| Theme UI từ style bible | `core/studio_core/export/theme.py::build_schemes` | `core/studio_core/export/theme_dart.py::theme_files` | WCAG AA | — |
| Audit folder game (quét/sửa/undo) | `core/studio_core/export/audit.py::scan` | `core/studio_core/export/audit.py::apply_fixes`, `core/studio_core/api/audit.py` | luôn backup + `undo` | — |
| Pack project thành package | `core/studio_core/store/pack.py::finalize_unity` | `core/studio_core/api/pack.py::build_pack` | — | — |
| DB / repo | `core/studio_core/store/repo.py::SqliteRepo` | `core/studio_core/store/db.py::Database` | — | — |
| Blob content-addressed | `core/studio_core/store/blobs.py::FsBlobStore` | `core/studio_core/store/export.py::export_copy` | blob không sửa tại chỗ | — |
| Event bus / WS | `core/studio_core/store/events.py::MemoryEventBus` | `core/studio_core/api/ws.py::events_ws` | `seq` đơn điệu | — |
| MCP server + tool | `core/studio_core/mcp/server.py::StudioTools` | `core/studio_core/mcp/server.py::build_server`, `core/studio_core/mcp/activity.py::ActivityReporter` | MCP chỉ proxy HTTP tới core | — |
| Gợi ý bước tiếp theo MCP | `core/studio_core/mcp/followup.py::follow_ups` | `core/studio_core/mcp/catalog.py::catalog_markdown` | — | — |
| CLI `agent` | `core/studio_core/mcp/agent_cli.py::run` | `core/studio_core/main.py::main` | — | — |
| Electron: spawn / giám sát core | `app/src/main/core.ts::CoreSupervisor` | `app/src/main/index.ts` | `EXIT_ALREADY_RUNNING` = attach | — |
| Electron: export asset ra đĩa | `app/src/main/assets.ts::AssetExporter` | `app/src/main/assets.ts::checkHash` | IPC phải qua `check*` | — |
| Electron: cài plugin agent | `app/src/main/agentInstall.ts::installAgent` | `app/src/main/enginePlugin.ts::installEnginePlugin` | — | — |
| Client API UI | `app/src/renderer/src/api/client.ts::createHttpApi` | `app/src/renderer/src/api/client.ts::StudioApi` | kiểu khớp `contracts.py` | — |
| WS client UI | `app/src/renderer/src/api/ws.ts::StudioSocket` | `app/src/renderer/src/stores/events.ts::applyEvents` | resync khi mất event | — |
| Kết nối / transport UI | `app/src/renderer/src/stores/connection.ts::useConnection` | `app/src/renderer/src/api/transport.ts::createRealTransport` | mock = `dev:mock` | — |
| Màn tạo run | `app/src/renderer/src/views/NewRunView.vue` | `app/src/renderer/src/components/RunPresetMenu.vue` | — | — |
| Màn chi tiết run | `app/src/renderer/src/views/RunDetailView.vue` | `app/src/renderer/src/components/BundleMenu.vue` | — | — |
| Flow editor | `app/src/renderer/src/views/FlowEditorView.vue` | `app/src/renderer/src/components/flow/FlowCanvas.vue`, `app/src/renderer/src/composables/flowGraph.ts` | undo tối đa 50 bước | — |
| Viewer asset | `app/src/renderer/src/components/viewers/AssetViewer.vue` | `app/src/renderer/src/components/viewers/detect.ts` | — | — |
| Kiểm Python core sau khi sửa | `scripts/check.mjs` | `core/pyproject.toml` | không chạy marker `live` | — |
