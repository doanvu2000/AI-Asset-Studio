# core_mcp — MCP server, agent CLI, plugin tích hợp

Gốc `core/studio_core/mcp/` + `integrations/`. MCP là proxy HTTP tới core, không chứa logic pipeline.

## Kết nối core
- `mcp/link.py::CoreLink` — `request(method, path)`, `upload`, `download`, `fetch_bytes`; attach core đang chạy qua lock file, nếu không có thì spawn core riêng (`launch_detached`, `READY_PREFIX` `STUDIO_CORE_READY`) và tắt khi xong; `CoreError(code, message)`.
- `mcp/lockfile.py::write_lock` · `read_lock` · `live_core` · `remove_lock` · `SpawnLock` — `<data_dir>/core.lock.json` (port, token, pid, version) + `core.spawn.lock` chặn 2 client spawn cùng lúc (`SPAWN_LOCK_WAIT_S` 60s); `EXIT_ALREADY_RUNNING` 3.
- `mcp/activity.py::ActivityReporter` — post `start` / `end` / `error` của mỗi tool call lên `/api/agent/activity` để app hiện live (`summarize`, `run_id_of`).

## Server
- `mcp/server.py::build_server` — dựng `StudioServer` + khai báo tool (`@mcp.tool`), resource `asset-studio://recipes`; `run_stdio` là entry stdio.
- `mcp/server.py::StudioTools` — logic từng tool: `list_recipes`, `describe_recipe`, `start_run`, `wait_run`, `resolve_gate`, `get_outputs`, `list_runs`, `list_versions`, `set_version_status`, `system_status`, `save_project`, `follow_up_options`, `import_files`, `export_to_engine`, `run_view` (JSON gọn + thumbnail).
- `mcp/server.py::StudioServer.call_tool` — bọc mọi tool bằng `ActivityReporter`.
- Nhóm tool khác trong `build_server`: GPU (`gpu_status`, `yield_gpu`, `resume_gpu`, `prioritize_run`), `control_service` (comfyui/blender), project (`create_project`, `update_project`), asset list/GDD (`get_asset_list`, `asset_list_from_gdd`, `run_asset_list`), QA (`project_qa`, `project_qa_fix`), `find_similar_assets` / `find_duplicate_assets`, style bible, `build_asset_pack`, `get_ui_theme`, `recipe_stats`, `engine_preview`, `generate` (start + wait + outputs).
- `mcp/followup.py::follow_ups` · `fits` · `looks_like_level` / `looks_like_tile` — gợi ý recipe tiếp theo cho 1 output.
- `mcp/catalog.py::catalog_markdown` · `load_yaml_recipes` · `main` — sinh catalog recipe markdown.
- `mcp/engine_preview.py::engine_preview` — POST `/api/steps/{id}/engine-preview`, poll `/api/engine-preview/jobs/{id}`, huỷ khi timeout.

## CLI
- `mcp/agent_cli.py::add_parser` · `run` — subcommand `studio-core agent ...` cho Claude Code / Codex (không cần MCP); `_inputs` parse `key=value`.
- `main.py::_parse` thêm subcommand `serve` và `agent`.

## Gói tích hợp (`integrations/`)
- `integrations/claude-plugin/` — `.claude-plugin/plugin.json`, `.mcp.json`, lệnh `commands/{make,pack,status}.md`, skill `skills/asset-studio/SKILL.md` + `references/recipes.md` (sinh từ catalog).
- `integrations/codex/` — `AGENTS.md`, `config.toml`, skill tương đương.
- `integrations/unity/com.assetstudio.bridge/` — Unity Editor: `StudioClient`, `StudioRuns`, `AssetStudioWindow`, `BridgeBatch`.
- `integrations/godot/addons/asset_studio/` — `plugin.gd`, `dock.gd`, `studio_client.gd`.
- App cài plugin cho agent: `app/src/main/agentInstall.ts::installAgent` (xem `app_main.md`).

## Invariant
- Tool trả lỗi dạng `ToolError("<code>: <message>")` từ `CoreError`.
- `start_run` nhận đường dẫn file tuyệt đối cho input ảnh/mesh/file (tự upload qua `CoreLink.upload`).
- `GatePolicy` = `human` (chờ người duyệt trong app) · `agent` (agent tự `resolve_gate`) · `auto_first` (tự chọn ứng viên đầu khi gate mở); lưu ở `params.$meta.gate_policy`.
- Test: `core/tests/test_agent_mcp.py`, `test_mcp_r12.py`, `test_p4b_mcp.py`, `test_p4b_lock.py`, `test_r7_followup_gate.py`.
